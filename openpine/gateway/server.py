"""OpenPine Web Gateway — FastAPI application factory."""

from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import cast

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openpine import __version__
from openpine._compat import structlog
from openpine.gateway.config import GatewayConfig
from openpine.gateway.deps import GatewayState
from openpine.gateway.security import (
    WebSocketAuditMiddleware,
    api_auth_dependency,
    audit_and_secure_request,
)
from openpine.gateway.routes import (
    accounts_data,
    achievements,
    backtest,
    dashboard,
    events,
    jobs,
    optimizer,
    orders_positions,
    pine_ops,
    pine_sources,
    settings,
    strategies,
    trading,
    tv_parity,
    version,
)
from openpine.gateway.worker_supervisor import (
    SupervisorConfig,
    WorkerSupervisor,
    worker_runtime_snapshot,
)

log = structlog.get_logger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, str(default)))
    except ValueError:
        return default


def _live_runner_requested(state: GatewayState) -> bool:
    """Return whether live/paper catch-up work is enabled for this gateway."""

    return bool(getattr(state.config, "live_enabled", False)) or _env_flag(
        "OPENPINE_ENABLE_LIVE_RUNNER"
    )


async def _stop_live_runner(runner) -> bool:
    """Request shutdown and return only after bounded runner quiescence."""

    runner.stop()
    wait_stopped = getattr(runner, "wait_stopped", None)
    if wait_stopped is None:
        return False
    return bool(await asyncio.shield(wait_stopped()))


def _thread_service_quiesced(service) -> bool:
    """Return false when a synchronous service still owns a live thread."""

    thread = getattr(service, "_thread", None)
    if thread is None:
        return True
    try:
        return not bool(thread.is_alive())
    except Exception:
        return False


async def _stop_runtime_services(*, runner=None, fetcher=None, supervisor=None) -> bool:
    """Attempt every shutdown step, preserving the first failure."""

    first_error: BaseException | None = None
    runner_quiesced = runner is None
    if runner is not None:
        try:
            runner_quiesced = await _stop_live_runner(runner)
        except BaseException as exc:
            first_error = exc

    fetcher_quiesced = fetcher is None
    if fetcher is not None:
        try:
            fetcher.stop()
            fetcher_quiesced = _thread_service_quiesced(fetcher)
        except BaseException as exc:
            if first_error is None:
                first_error = exc

    supervisor_quiesced = supervisor is None
    if supervisor is not None:
        try:
            supervisor_quiesced = bool(await asyncio.shield(supervisor.stop()))
        except BaseException as exc:
            if first_error is None:
                first_error = exc

    if first_error is not None:
        raise first_error
    return runner_quiesced and fetcher_quiesced and supervisor_quiesced


def _run_background_services(
    stop_event,
    live_runner_enabled: bool = True,
    ready_event=None,
    heartbeat=None,
) -> None:
    """Run market refresh and paper/live catch-up outside the API process."""

    async def _main() -> None:
        from openpine.data.periodic_fetcher import PeriodicBarFetcher, RefreshConfig
        from openpine.gateway.live_runner import LiveStrategyRunner, RunnerConfig

        state = GatewayState()
        fetcher = PeriodicBarFetcher(
            config=RefreshConfig(
                interval_seconds=60.0, lookback_bars=2, source_timeframe="1m"
            ),
            registry=state.strategy_registry,
            orchestrator=state.orchestrator,
        )
        runner = None
        if live_runner_enabled:
            runner = LiveStrategyRunner(
                config=RunnerConfig(check_interval_seconds=5.0),
                registry=state.strategy_registry,
                orchestrator=state.orchestrator,
                storage=state.storage,
                artifact_store=state.artifact_store,
                state_store=state.state_store,
            )

        async def _achievement_tick(stop_event: asyncio.Event) -> None:
            """Recompute achievements every 5 min. Self-heal path that
            catches any event-hook misses."""
            interval = 300.0
            while not stop_event.is_set():
                try:
                    state.achievement_engine.recompute_stats()
                except Exception as exc:
                    log.warning("achievement_tick_error", error=str(exc))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                except TimeoutError:
                    continue

        ach_stop = asyncio.Event()
        ach_task = asyncio.create_task(_achievement_tick(ach_stop))
        try:
            fetcher.start()
            if runner is not None:
                runner.start()
            else:
                log.info("background_live_runner_disabled")
            if heartbeat is not None:
                heartbeat.value = time.time()
            if ready_event is not None:
                ready_event.set()
            log.info("gateway_background_services_started")
            while not stop_event.is_set():
                if heartbeat is not None:
                    heartbeat.value = time.time()
                await asyncio.sleep(1.0)
        finally:
            ach_stop.set()
            ach_task.cancel()
            try:
                await ach_task
            except (asyncio.CancelledError, Exception):
                pass
            safe_to_close = await _stop_runtime_services(
                runner=runner,
                fetcher=fetcher,
            )
            if safe_to_close:
                state.close()
            else:
                log.critical(
                    "gateway_background_state_close_skipped_work_still_running"
                )
            log.info("gateway_background_services_stopped")

    asyncio.run(_main())


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Initialize shared state on startup, close on shutdown."""
    state = GatewayState()
    state._startup_time = time.time()
    app.state.gateway = state
    log.info("gateway_started", sqlite=str(state.config.sqlite_path))

    # Mark stuck "running" backtests as failed (gateway was restarted)
    try:
        stuck_runs = state.storage.execute(
            "SELECT run_id FROM backtest_runs WHERE status = 'running'"
        ).fetchall()
        if stuck_runs:
            now = int(time.time() * 1000)
            for (run_id,) in stuck_runs:
                state.storage.execute(
                    "UPDATE backtest_runs SET status = 'failed', error_message = ?, finished_at = ?, updated_at = ? WHERE run_id = ?",
                    ("Gateway restarted while backtest was running", now, now, run_id),
                )
            state.storage.commit()
            log.info("marked_stuck_backtests_failed", count=len(stuck_runs))
    except Exception as exc:
        log.warning("stuck_backtest_cleanup_error", error=str(exc))

    # Keep heavy recurring work out of the API process. The worker handles
    # restart catch-up for bars and mini-backtests without starving gateway.
    background_supervisor: WorkerSupervisor | None = None
    fetcher = None
    runner = None
    state._background_worker_process = None
    state._background_worker_supervisor = None
    background_worker_enabled = _env_flag("OPENPINE_ENABLE_BACKGROUND_WORKER", True)
    live_runner_requested = _live_runner_requested(state)
    try:
        if background_worker_enabled:
            ctx = mp.get_context("spawn")

            def _process_factory():
                stop_event = ctx.Event()
                ready_event = ctx.Event()
                heartbeat = (
                    ctx.Value("d", 0.0)
                    if hasattr(ctx, "Value")
                    else SimpleNamespace(value=0.0)
                )
                process = ctx.Process(
                    target=_run_background_services,
                    args=(stop_event, live_runner_requested, ready_event, heartbeat),
                    name="openpine-gateway-background",
                    daemon=True,
                )
                return process, stop_event, ready_event, heartbeat

            registry = getattr(state, "strategy_registry", None)
            reset_circuit = getattr(registry, "reset_worker_circuit", None)
            if reset_circuit is not None:
                reset_circuit()
            trip_circuit = getattr(registry, "trip_worker_circuit", None)
            pause_all = getattr(registry, "pause_all_enabled", lambda: 0)

            def fail_safe():
                with state.strategy_activation_lock:
                    if trip_circuit is not None:
                        return trip_circuit("background_worker_unavailable")
                    return pause_all()

            background_supervisor = WorkerSupervisor(
                _process_factory,
                fail_safe=fail_safe,
                config=SupervisorConfig(
                    poll_interval_seconds=_env_float(
                        "OPENPINE_WORKER_MONITOR_INTERVAL_SECONDS", 1.0
                    ),
                    backoff_initial_seconds=_env_float(
                        "OPENPINE_WORKER_RESTART_BACKOFF_SECONDS", 1.0
                    ),
                    backoff_max_seconds=_env_float(
                        "OPENPINE_WORKER_RESTART_BACKOFF_MAX_SECONDS", 30.0
                    ),
                    max_restarts=_env_int("OPENPINE_WORKER_MAX_RESTARTS", 3),
                    restart_window_seconds=_env_float(
                        "OPENPINE_WORKER_RESTART_WINDOW_SECONDS", 300.0
                    ),
                    heartbeat_stale_seconds=_env_float(
                        "OPENPINE_WORKER_HEARTBEAT_STALE_SECONDS", 15.0
                    ),
                ),
                on_process=lambda process: setattr(
                    state, "_background_worker_process", process
                ),
            )
            state._background_worker_supervisor = background_supervisor
            background_supervisor.start()
        else:
            log.info("gateway_background_worker_disabled")

        # Optional in-process fetcher kept for tests/debugging only.
        fetcher = None
        if _env_flag("OPENPINE_ENABLE_PERIODIC_FETCHER"):
            from openpine.data.periodic_fetcher import PeriodicBarFetcher, RefreshConfig

            fetcher_config = RefreshConfig(
                interval_seconds=60.0, lookback_bars=2, source_timeframe="1m"
            )
            fetcher = PeriodicBarFetcher(
                config=fetcher_config,
                registry=state.strategy_registry,
                orchestrator=state.orchestrator,
            )
            fetcher.start()
            state._fetcher = fetcher  # expose to dashboard route
            log.info("periodic_fetcher_started", interval=fetcher_config.interval_seconds)
        else:
            state._fetcher = None
            log.info("periodic_fetcher_disabled")

        # Start live strategy runner only when explicitly enabled. It runs
        # mini-backtests in the gateway process and can starve API responses on
        # active paper/live strategies.
        runner = None
        in_process_live_runner_enabled = live_runner_requested and not background_worker_enabled
        if in_process_live_runner_enabled:
            from openpine.gateway.live_runner import LiveStrategyRunner, RunnerConfig

            runner = LiveStrategyRunner(
                config=RunnerConfig(check_interval_seconds=5.0),
                registry=state.strategy_registry,
                orchestrator=state.orchestrator,
                storage=state.storage,
                artifact_store=state.artifact_store,
            )
            runner.start()
            state._live_runner = runner
            log.info("live_runner_started")
        elif live_runner_requested:
            state._live_runner = None
            log.info("live_runner_delegated_to_background_worker")
        else:
            state._live_runner = None
            log.info("live_runner_disabled")

        yield
    finally:
        safe_to_close = False
        try:
            safe_to_close = await _stop_runtime_services(
                runner=runner,
                fetcher=fetcher,
                supervisor=background_supervisor,
            )
        finally:
            if safe_to_close:
                state.close()
            else:
                log.critical("gateway_state_close_skipped_fail_safe_still_running")
            log.info("gateway_stopped")


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = config or GatewayConfig()

    app = FastAPI(
        title="OpenPine Gateway",
        description="Web API for the OpenPine Pine stack — strategies, backtests, live trading, and market data.",
        version=__version__,
        lifespan=lifespan,
        docs_url=None if cfg.environment == "production" else "/docs",
        redoc_url=None if cfg.environment == "production" else "/redoc",
        openapi_url=None if cfg.environment == "production" else "/openapi.json",
    )
    app.state.gateway_config = cfg

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.middleware("http")(audit_and_secure_request)
    app.add_middleware(WebSocketAuditMiddleware)

    # Mount route modules under /api
    api_prefix = cfg.api_prefix
    api_dependencies = [
        Depends(api_auth_dependency(cfg.auth_token, cfg.auth_principal))
    ]
    app.include_router(dashboard.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(
        pine_sources.router, prefix=api_prefix, dependencies=api_dependencies
    )
    app.include_router(pine_ops.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(strategies.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(backtest.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(trading.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(
        orders_positions.router, prefix=api_prefix, dependencies=api_dependencies
    )
    app.include_router(events.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(jobs.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(settings.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(
        accounts_data.router, prefix=api_prefix, dependencies=api_dependencies
    )
    app.include_router(optimizer.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(tv_parity.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(
        achievements.router, prefix=api_prefix, dependencies=api_dependencies
    )
    app.include_router(version.router, prefix=api_prefix, dependencies=api_dependencies)

    generated_openapi = app.openapi

    def openapi_with_websocket_contract() -> dict[str, object]:
        schema = cast(dict[str, object], generated_openapi())
        schema["x-openpine-websocket-paths"] = [f"{api_prefix}/ws/events"]
        return schema

    app.openapi = openapi_with_websocket_contract

    @app.get("/health")
    async def health() -> dict[str, object]:
        state = getattr(app.state, "gateway", None)
        worker = worker_runtime_snapshot(state)
        degraded = bool(
            worker["degraded"]
            or (
                worker["enabled"]
                and (
                    not worker["alive"]
                    or not worker.get("ready")
                    or worker.get("heartbeat_stale")
                )
            )
        )
        response: dict[str, object] = {
            "status": "degraded" if degraded else "ok",
            "version": __version__,
        }
        if cfg.environment != "production":
            response["runtime"] = {"background_worker": worker}
        return response

    @app.get("/")
    async def root() -> dict[str, str]:
        response = {
            "service": "OpenPine Gateway",
            "version": __version__,
            "api": api_prefix,
        }
        if cfg.environment != "production":
            response["docs"] = "/docs"
        return response

    return app
