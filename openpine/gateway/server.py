"""OpenPine Web Gateway — FastAPI application factory."""

from __future__ import annotations

import time
import os
import asyncio
import multiprocessing as mp
from contextlib import asynccontextmanager
from typing import AsyncIterator

from openpine._compat import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from openpine import __version__
from openpine.gateway.config import GatewayConfig
from openpine.gateway.deps import GatewayState
from openpine.gateway.routes import (
    accounts_data,
    backtest,
    dashboard,
    events,
    optimizer,
    orders_positions,
    pine_ops,
    pine_sources,
    settings,
    strategies,
    trading,
    tv_parity,
)

log = structlog.get_logger(__name__)


def _env_flag(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.lower() in {"1", "true", "yes", "on"}


def _run_background_services(stop_event) -> None:
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
        runner = LiveStrategyRunner(
            config=RunnerConfig(check_interval_seconds=5.0),
            registry=state.strategy_registry,
            orchestrator=state.orchestrator,
            storage=state.storage,
            artifact_store=state.artifact_store,
            state_store=state.state_store,
        )
        try:
            fetcher.start()
            runner.start()
            log.info("gateway_background_services_started")
            while not stop_event.is_set():
                await asyncio.sleep(1.0)
        finally:
            runner.stop()
            fetcher.stop()
            state.close()
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
    background_process = None
    background_stop = None
    state._background_worker_process = None
    if _env_flag("OPENPINE_ENABLE_BACKGROUND_WORKER", True):
        ctx = mp.get_context("fork")
        background_stop = ctx.Event()
        background_process = ctx.Process(
            target=_run_background_services,
            args=(background_stop,),
            name="openpine-gateway-background",
            daemon=True,
        )
        background_process.start()
        state._background_worker_process = background_process
        log.info("gateway_background_worker_started", pid=background_process.pid)
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
    live_runner_enabled = state.config.live_enabled or _env_flag(
        "OPENPINE_ENABLE_LIVE_RUNNER"
    )
    if live_runner_enabled:
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
    else:
        state._live_runner = None
        log.info("live_runner_disabled")

    yield

    if runner is not None:
        runner.stop()
    if fetcher is not None:
        fetcher.stop()
    if background_stop is not None:
        background_stop.set()
    if background_process is not None:
        background_process.join(timeout=10)
        if background_process.is_alive():
            background_process.terminate()
            background_process.join(timeout=5)
    state.close()
    log.info("gateway_stopped")


def create_app(config: GatewayConfig | None = None) -> FastAPI:
    """Create and configure the FastAPI application."""
    cfg = config or GatewayConfig()

    app = FastAPI(
        title="OpenPine Gateway",
        description="Web API for the OpenPine Pine stack — strategies, backtests, live trading, and market data.",
        version=__version__,
        lifespan=lifespan,
    )

    # CORS
    app.add_middleware(
        CORSMiddleware,
        allow_origins=cfg.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Mount route modules under /api
    api_prefix = cfg.api_prefix
    app.include_router(dashboard.router, prefix=api_prefix)
    app.include_router(pine_sources.router, prefix=api_prefix)
    app.include_router(pine_ops.router, prefix=api_prefix)
    app.include_router(strategies.router, prefix=api_prefix)
    app.include_router(backtest.router, prefix=api_prefix)
    app.include_router(trading.router, prefix=api_prefix)
    app.include_router(orders_positions.router, prefix=api_prefix)
    app.include_router(events.router, prefix=api_prefix)
    app.include_router(settings.router, prefix=api_prefix)
    app.include_router(accounts_data.router, prefix=api_prefix)
    app.include_router(optimizer.router, prefix=api_prefix)
    app.include_router(tv_parity.router, prefix=api_prefix)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok", "version": __version__}

    @app.get("/")
    async def root() -> dict[str, str]:
        return {
            "service": "OpenPine Gateway",
            "version": __version__,
            "docs": "/docs",
            "api": api_prefix,
        }

    return app
