"""OpenPine Web Gateway — FastAPI application factory."""

from __future__ import annotations

import asyncio
import base64
import json
import multiprocessing as mp
import os
import time
import uuid
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

    from openpine.live_release_gate import live_execution_enabled

    if not live_execution_enabled():
        return False
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


def _process_refreshed_strategy_bars(
    *,
    fanout,
    executor,
    scheduler,
    market_key,
    bars,
    job_store=None,
    stack_id: str = "openpine-5.0",
    lease_owner: str = "paper-strategy-worker",
) -> None:
    from openpine.jobs import JobType
    from openpine.jobs.persist import JobV1Error

    kind_by_type = {
        JobType.PAPER_BAR_PROCESS: "paper",
        JobType.OBSERVE_BAR_PROCESS: "observe",
        JobType.LIVE_BAR_PROCESS: "live",
    }

    def specification(job):
        payload = dict(getattr(job, "input", None) or {})
        encoded_payload = base64.urlsafe_b64encode(
            json.dumps(
                payload,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).decode("ascii")
        refs = [
            f"strategy:{payload.get('strategy_id') or job.strategy_id}",
            f"artifact:{payload.get('artifact_id') or ''}",
            f"params:{payload.get('params_hash') or ''}",
            "inline:openpine.strategy-job-input.v1:" + encoded_payload,
        ]
        semantic_profile = payload.get("semantic_profile")
        if semantic_profile:
            refs.append(f"semantic_profile:{semantic_profile}")
        return {
            "job_id": str(job.id),
            "kind": kind_by_type[job.job_type],
            "actor": "paper-strategy-worker",
            "idempotency_key": job.idempotency_key,
            "input_artifact_refs": refs,
            "stack_id": stack_id,
        }

    def persist(job, precreated=None):
        if job_store is None:
            return None, True
        record = precreated or job_store.create(**specification(job))
        state = str(record["state"])
        if state == "SUCCEEDED":
            scheduler.mark_done(job.id, {"status": "already_persisted"})
            return record, False
        if state in {"FAILED", "LOST"}:
            record = job_store.retry(str(job.id))
            state = str(record["state"])
        if state == "RUNNING":
            return record, False
        if state != "QUEUED":
            return record, False
        now_ms = int(time.time() * 1000)
        return (
            job_store.mark_running(
                str(job.id),
                lease_owner=lease_owner,
                lease_deadline_utc_ms=now_ms + 15 * 60_000,
                now_ms=now_ms,
                expected_version=int(record["version"]),
            ),
            True,
        )

    fanout_results = [
        (bar, fanout.process_source_bar(bar))
        for bar in bars
    ]
    all_jobs = [
        job
        for _bar, fanout_result in fanout_results
        for job in fanout_result.jobs
    ]
    precreated: dict[str, object] = {}
    if job_store is not None and all_jobs:
        try:
            create_batch = getattr(job_store, "create_batch", None)
            records = (
                create_batch([specification(job) for job in all_jobs])
                if callable(create_batch)
                else [job_store.create(**specification(job)) for job in all_jobs]
            )
            precreated = {
                str(record["job_id"]): record
                for record in records
            }
        except JobV1Error as exc:
            for job in all_jobs:
                scheduler.mark_failed(job.id, "JOB_PERSISTENCE_FAILED")
            log.error(
                "strategy_bar_job_batch_persistence_failed",
                jobs=len(all_jobs),
                error=str(exc),
            )
            return

    for bar, fanout_result in fanout_results:
        for job in fanout_result.jobs:
            try:
                persisted, claimed = persist(job, precreated.get(str(job.id)))
            except JobV1Error as exc:
                scheduler.mark_failed(job.id, "JOB_PERSISTENCE_FAILED")
                log.error(
                    "strategy_bar_job_persistence_failed",
                    job_id=job.id,
                    strategy_id=job.strategy_id,
                    error=str(exc),
                )
                continue
            persisted_state = str(persisted["state"]) if persisted is not None else None
            if not claimed:
                continue
            if job.job_type == JobType.LIVE_BAR_PROCESS:
                scheduler.mark_failed(job.id, "LIVE_RC_BLOCKED")
                if job_store is not None and persisted_state == "RUNNING":
                    job_store.mark_failed(
                        str(job.id),
                        error_code="LIVE_RC_BLOCKED",
                        lease_owner=lease_owner,
                    )
                log.warning(
                    "strategy_bar_live_blocked",
                    strategy_id=job.strategy_id,
                    market_key=str(market_key),
                    bar_time=getattr(bar, "time", None),
                )
                continue
            result = executor.process(job)
            status = str(getattr(result, "status", "failed"))
            if job_store is not None and persisted_state == "RUNNING":
                try:
                    if status in {"done", "skipped"}:
                        result_refs = []
                        snapshot_id = getattr(result, "snapshot_id", None)
                        if snapshot_id:
                            result_refs.append(f"snapshot:{snapshot_id}")
                        job_store.mark_succeeded(
                            str(job.id),
                            lease_owner=lease_owner,
                            result_artifact_refs=result_refs,
                        )
                    else:
                        job_store.mark_failed(
                            str(job.id),
                            error_code=str(
                                getattr(result, "error", None)
                                or "PAPER_EXECUTION_FAILED"
                            ),
                            lease_owner=lease_owner,
                        )
                except JobV1Error as exc:
                    log.warning(
                        "strategy_bar_terminal_transition_fenced",
                        job_id=job.id,
                        strategy_id=job.strategy_id,
                        error=str(exc),
                    )
            logger = log.info if status in {"done", "skipped"} else log.error
            logger(
                "strategy_bar_processed",
                strategy_id=getattr(result, "strategy_id", job.strategy_id),
                market_key=str(market_key),
                bar_time=getattr(result, "bar_time", getattr(bar, "time", None)),
                status=status,
                snapshot_id=getattr(result, "snapshot_id", None),
                trades_recorded=getattr(result, "trades_recorded", 0),
                error=getattr(result, "error", None),
            )


def _strategy_job_from_persisted(record):
    from openpine.jobs import Job, JobType
    from openpine.jobs.persist import JobV1Error

    prefix = "inline:openpine.strategy-job-input.v1:"
    encoded = next(
        (
            str(ref)[len(prefix) :]
            for ref in record.get("input_artifact_refs", ())
            if str(ref).startswith(prefix)
        ),
        None,
    )
    if not encoded:
        raise JobV1Error("persisted strategy job input artifact is missing")
    try:
        payload = json.loads(base64.urlsafe_b64decode(encoded).decode("utf-8"))
    except (ValueError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise JobV1Error("persisted strategy job input artifact is invalid") from exc
    if not isinstance(payload, dict):
        raise JobV1Error("persisted strategy job input artifact must be an object")
    type_by_kind = {
        "paper": JobType.PAPER_BAR_PROCESS,
        "observe": JobType.OBSERVE_BAR_PROCESS,
        "live": JobType.LIVE_BAR_PROCESS,
    }
    try:
        job_type = type_by_kind[str(record["kind"])]
        strategy_id = str(payload["strategy_id"])
        idempotency_key = str(record["idempotency_key"])
    except (KeyError, TypeError) as exc:
        raise JobV1Error("persisted strategy job identity is incomplete") from exc
    if not strategy_id or not idempotency_key:
        raise JobV1Error("persisted strategy job identity is incomplete")
    return Job(
        id=str(record["job_id"]),
        job_type=job_type,
        strategy_id=strategy_id,
        idempotency_key=idempotency_key,
        serialization_key=strategy_id,
        priority=50,
        input=payload,
    )


def _recover_persisted_strategy_jobs(
    *, executor, scheduler, job_store, stack_id: str, lease_owner: str
) -> int:
    from openpine.jobs.persist import JobV1Error

    job_store.recover_worker_leases(
        active_lease_owner=lease_owner,
        now_ms=int(time.time() * 1000),
        kinds=("paper", "observe", "live"),
    )
    records: list[tuple[str, dict]] = []
    for state_name in ("LOST", "QUEUED"):
        for kind in ("paper", "observe", "live"):
            cursor = None
            while True:
                page = job_store.list_jobs(
                    kind=kind,
                    state=state_name,
                    cursor=cursor,
                    limit=1_000,
                )
                records.extend((state_name, record) for record in page["items"])
                cursor = page.get("cursor")
                if not cursor:
                    break

    pending_jobs = []
    for state_name, record in records:
        try:
            job = _strategy_job_from_persisted(record)
        except JobV1Error as exc:
            failed = record
            if state_name == "LOST":
                try:
                    failed = job_store.retry(str(record["job_id"]))
                except JobV1Error as retry_exc:
                    job_store.cancel(
                        str(record["job_id"]),
                        idempotency_key=(
                            f"{record['job_id']}:recovery-poison-canceled"
                        ),
                    )
                    log.error(
                        "persisted_strategy_job_retry_exhausted",
                        job_id=record.get("job_id"),
                        error=str(retry_exc),
                    )
                    continue
            job_store.mark_failed(
                str(record["job_id"]),
                error_code="PERSISTED_JOB_INPUT_INVALID",
                expected_version=int(failed["version"]),
            )
            log.error(
                "persisted_strategy_job_invalid",
                job_id=record.get("job_id"),
                error=str(exc),
            )
            continue
        pending_jobs.append(job)

    pending_jobs.sort(
        key=lambda job: (
            str(job.strategy_id or ""),
            int((job.input or {}).get("bar_time") or 0),
            str(job.id),
        )
    )
    recovered = 0
    for job in pending_jobs:
        job = scheduler.enqueue(job)
        fanout = SimpleNamespace(
            process_source_bar=lambda _bar, current=job: SimpleNamespace(
                jobs=(current,)
            )
        )
        marker = SimpleNamespace(time=int((job.input or {}).get("bar_time") or 0))
        _process_refreshed_strategy_bars(
            fanout=fanout,
            executor=executor,
            scheduler=scheduler,
            market_key=(job.input or {}).get("instrument_key") or "persisted",
            bars=[marker],
            job_store=job_store,
            stack_id=stack_id,
            lease_owner=lease_owner,
        )
        recovered += 1
    return recovered


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
        from openpine.storage.strategy_ledger import StrategyLedger
        from openpine.workers.strategy_fanout import (
            StrategyBarFanout,
            StrategyBarFanoutConfig,
        )
        from openpine.workers.strategy_job_executor import StrategyJobExecutor

        state = GatewayState()
        from openpine.jobs import JobScheduler

        paper_scheduler = getattr(state, "scheduler", None) or JobScheduler()
        paper_job_store = getattr(state, "job_store", None)
        paper_lease_owner = f"paper-strategy-worker:{uuid.uuid4().hex}"
        admission = getattr(state, "admission_identity", None)
        paper_stack_id = admission.stack_manifest_hash if admission is not None else "openpine-5.0"
        fanout = StrategyBarFanout(
            registry=state.strategy_registry,
            orchestrator=state.orchestrator,
            scheduler=paper_scheduler,
            config=StrategyBarFanoutConfig(
                persist_source=False,
                persist_aggregates=False,
                source="periodic",
            ),
        )
        executor = StrategyJobExecutor(
            registry=state.strategy_registry,
            orchestrator=state.orchestrator,
            scheduler=paper_scheduler,
            state_store=state.state_store,
            ledger=StrategyLedger(state.storage),
            artifact_store=state.artifact_store,
            execution_lease=(
                (
                    lambda job_id: paper_job_store.heartbeat_lease(
                        job_id,
                        lease_owner=paper_lease_owner,
                    )
                )
                if paper_job_store is not None
                else None
            ),
            publication_lease=(
                (
                    lambda job_id: paper_job_store.publication_lease(
                        job_id,
                        lease_owner=paper_lease_owner,
                    )
                )
                if paper_job_store is not None
                else None
            ),
        )
        recovered_strategy_jobs = (
            _recover_persisted_strategy_jobs(
                executor=executor,
                scheduler=paper_scheduler,
                job_store=paper_job_store,
                stack_id=paper_stack_id,
                lease_owner=paper_lease_owner,
            )
            if paper_job_store is not None
            else 0
        )
        if recovered_strategy_jobs:
            log.info(
                "persisted_strategy_jobs_recovered",
                count=recovered_strategy_jobs,
            )

        def dispatch_refreshed_bars(market_key, bars) -> None:
            recovered = (
                _recover_persisted_strategy_jobs(
                    executor=executor,
                    scheduler=paper_scheduler,
                    job_store=paper_job_store,
                    stack_id=paper_stack_id,
                    lease_owner=paper_lease_owner,
                )
                if paper_job_store is not None
                else 0
            )
            if recovered:
                log.info("persisted_strategy_jobs_recovered", count=recovered)
            _process_refreshed_strategy_bars(
                fanout=fanout,
                executor=executor,
                scheduler=paper_scheduler,
                market_key=market_key,
                bars=bars,
                job_store=paper_job_store,
                stack_id=paper_stack_id,
                lease_owner=paper_lease_owner,
            )

        fetcher = PeriodicBarFetcher(
            config=RefreshConfig(interval_seconds=60.0, lookback_bars=2, source_timeframe="1m"),
            registry=state.strategy_registry,
            orchestrator=state.orchestrator,
            on_bars_refreshed=dispatch_refreshed_bars,
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

        async def _paper_recovery_tick(stop_event: asyncio.Event) -> None:
            interval = 30.0
            while not stop_event.is_set():
                if paper_job_store is not None:
                    try:
                        recovered = await asyncio.to_thread(
                            _recover_persisted_strategy_jobs,
                            executor=executor,
                            scheduler=paper_scheduler,
                            job_store=paper_job_store,
                            stack_id=paper_stack_id,
                            lease_owner=paper_lease_owner,
                        )
                        if recovered:
                            log.info(
                                "persisted_strategy_jobs_recovered",
                                count=recovered,
                            )
                    except Exception as exc:
                        log.error("paper_recovery_tick_error", error=str(exc))
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=interval)
                except TimeoutError:
                    continue

        ach_stop = asyncio.Event()
        ach_task = asyncio.create_task(_achievement_tick(ach_stop))
        recovery_stop = asyncio.Event()
        recovery_task = asyncio.create_task(_paper_recovery_tick(recovery_stop))
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
            recovery_stop.set()
            try:
                await recovery_task
            except (asyncio.CancelledError, Exception):
                pass
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
                log.critical("gateway_background_state_close_skipped_work_still_running")
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
                    ctx.Value("d", 0.0) if hasattr(ctx, "Value") else SimpleNamespace(value=0.0)
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
                on_process=lambda process: setattr(state, "_background_worker_process", process),
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
    api_dependencies = [Depends(api_auth_dependency(cfg.auth_token, cfg.auth_principal))]
    app.include_router(dashboard.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(pine_sources.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(pine_ops.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(strategies.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(backtest.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(trading.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(orders_positions.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(events.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(jobs.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(settings.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(accounts_data.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(optimizer.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(tv_parity.router, prefix=api_prefix, dependencies=api_dependencies)
    app.include_router(achievements.router, prefix=api_prefix, dependencies=api_dependencies)
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
                    not worker["alive"] or not worker.get("ready") or worker.get("heartbeat_stale")
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
