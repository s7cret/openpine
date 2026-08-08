"""Dashboard routes — aggregated system overview."""

from __future__ import annotations

import asyncio
import time

from fastapi import APIRouter, Depends

from openpine._compat import structlog
from openpine.gateway.deps import (
    GatewayState,
    get_state,
)
from openpine.gateway.schemas import (
    DashboardResponse,
    JobSummary,
    StrategySummary,
)
from openpine.gateway.worker_supervisor import worker_runtime_snapshot
from openpine.gateway.ws_manager import ws_manager
from openpine.jobs import JobStatus

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])
STATE_DEP = Depends(get_state)


@router.get("", response_model=DashboardResponse)
async def dashboard(
    state: GatewayState = STATE_DEP,
) -> DashboardResponse:
    """Aggregated system overview."""
    registry = state.strategy_registry
    scheduler = state.scheduler

    # Strategies
    strategy_instances = await asyncio.to_thread(registry.list_strategies)
    strategies = []
    for s in strategy_instances:
        health = await _strategy_health_async(state, s)
        strategies.append(
            StrategySummary(
                strategy_id=s.strategy_id,
                name=s.name,
                symbol=s.symbol,
                timeframe=s.timeframe,
                mode=s.mode,
                status=s.status,
                enabled=s.enabled,
                health=health,
            )
        )

    # Jobs: merge in-memory scheduler jobs with persistent backtest runs so
    # the dashboard survives gateway restarts and still shows recent work.
    all_jobs = scheduler.list_jobs()
    persistent_jobs = await asyncio.to_thread(_persistent_jobs, state)
    recent_jobs = [
        {
            "id": j.id,
            "type": j.job_type.value,
            "status": j.status.value,
            "strategy_id": j.strategy_id,
            "created_at": j.created_at,
            "started_at": j.started_at,
            "finished_at": j.finished_at,
            "error": j.error,
            "input": j.input,
            "result": j.result,
            "progress": ws_manager.get_progress(j.id),
        }
        for j in all_jobs
    ] + persistent_jobs
    jobs = JobSummary(
        pending=sum(1 for j in all_jobs if j.status == JobStatus.PENDING)
        + _count_jobs(persistent_jobs, "pending"),
        running=sum(1 for j in all_jobs if j.status == JobStatus.RUNNING)
        + _count_jobs(persistent_jobs, "running"),
        done=sum(1 for j in all_jobs if j.status == JobStatus.DONE)
        + _count_jobs(persistent_jobs, "done"),
        failed=sum(1 for j in all_jobs if j.status == JobStatus.FAILED)
        + _count_jobs(persistent_jobs, "failed"),
        recent=sorted(
            recent_jobs, key=lambda x: x.get("created_at") or 0, reverse=True
        )[:20],
    )

    # SQLite and manifest access are bounded, but still blocking I/O. Keep them
    # off the event loop so Dashboard polling cannot delay /health.
    last_event_ts = await asyncio.to_thread(_last_event_time, state)
    last_bar_update_ts = await asyncio.to_thread(
        _last_bar_update, state, strategy_instances
    )

    return DashboardResponse(
        strategies=strategies,
        jobs=jobs,
        kill_switch=state._risk_kill_switch[0],
        uptime_seconds=time.time() - state._startup_time,
        last_event_time=last_event_ts,
        last_bar_update=last_bar_update_ts,
        runtime_health={"background_worker": worker_runtime_snapshot(state)},
    )


def _last_event_time(state: GatewayState) -> int | None:
    """Read the latest event timestamp with old-schema compatibility."""

    try:
        cols = {
            r[1] for r in state.storage.execute("PRAGMA table_info(events)").fetchall()
        }
        ts_col = "timestamp_ms" if "timestamp_ms" in cols else "created_at"
        row = state.storage.execute(f"SELECT MAX({ts_col}) FROM events").fetchone()
        return row[0] if row and row[0] else None
    except Exception:
        return None


def _last_bar_update(state: GatewayState, strategies: list) -> int | None:
    """Return fetcher state or latest manifest/index metadata, never bar rows."""

    fetcher = getattr(state, "_fetcher", None)
    if fetcher is not None and fetcher.last_fetch_at is not None:
        return fetcher.last_fetch_at
    try:
        enabled = [strategy for strategy in strategies if strategy.enabled]
        if not enabled:
            return None
        from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

        seen_symbols: set[str] = set()
        latest_ts = 0
        now_ms = int(time.time() * 1000)
        for strategy in enabled[:5]:
            symbol = strategy.symbol.upper()
            if symbol in seen_symbols:
                continue
            seen_symbols.add(symbol)
            try:
                query = BarQuery(
                    instrument=InstrumentKey(
                        exchange=strategy.exchange.lower(),
                        market=strategy.market_type.lower(),
                        symbol=symbol,
                    ),
                    timeframe=parse_timeframe("1m"),
                    start_ms=now_ms - 24 * 3600 * 1000,
                    end_ms=now_ms,
                    source="storage",
                    gap_policy="allow_with_metadata",
                )
                bar_time = state.orchestrator.latest_bar_time(query)
                if bar_time is not None:
                    latest_ts = max(latest_ts, int(bar_time))
            except Exception:
                continue
        return latest_ts or None
    except Exception:
        return None


def _count_jobs(jobs: list[dict], status: str) -> int:
    return sum(1 for job in jobs if job.get("status") == status)


def _persistent_jobs(state: GatewayState) -> list[dict[str, object]]:
    try:
        rows = state.storage.execute("""
            SELECT run_id, strategy_id, status, started_at, finished_at, created_at, error_message
            FROM backtest_runs
            ORDER BY COALESCE(created_at, started_at, 0) DESC
            LIMIT 20
            """).fetchall()
    except Exception:
        return []

    jobs: list[dict[str, object]] = []
    for run_id, strategy_id, status, started_at, finished_at, created_at, error in rows:
        normalized = _normalize_job_status(str(status or "pending"))
        jobs.append(
            {
                "id": run_id,
                "type": "backtest",
                "status": normalized,
                "strategy_id": strategy_id,
                "created_at": created_at or started_at,
                "started_at": started_at,
                "finished_at": finished_at,
                "error": error,
            }
        )
    return jobs


def _normalize_job_status(status: str) -> str:
    value = status.lower()
    if value in {"done", "completed", "success", "succeeded"}:
        return "done"
    if value in {"failed", "error"}:
        return "failed"
    if value in {"running", "queued", "pending"}:
        return "running" if value == "running" else "pending"
    if value == "cancelled":
        return "failed"
    return value


def _strategy_health(state: GatewayState, strategy) -> dict[str, object]:
    now_ms = int(time.time() * 1000)
    last_order = None
    try:
        row = state.storage.execute(
            """
            SELECT order_id, status, side, symbol, created_at, updated_at
            FROM orders
            WHERE strategy_id = ?
            ORDER BY created_at DESC
            LIMIT 1
            """,
            (strategy.strategy_id,),
        ).fetchone()
        if row:
            last_order = {
                "order_id": row[0],
                "status": row[1],
                "side": row[2],
                "symbol": row[3],
                "created_at": row[4],
                "updated_at": row[5],
            }
    except Exception:
        pass

    last_bar_time = None
    data_lag_seconds = None
    metadata_ok = True
    try:
        from marketdata_provider.contracts import (
            BarQuery,
            InstrumentKey,
            parse_timeframe,
        )

        tf = parse_timeframe("1m")
        key = InstrumentKey(
            exchange=strategy.exchange.lower(),
            market=strategy.market_type.lower(),
            symbol=strategy.symbol.upper(),
        )
        query = BarQuery(
            instrument=key,
            timeframe=tf,
            start_ms=now_ms - 6 * 3600 * 1000,
            end_ms=now_ms,
            source="storage",
            gap_policy="allow_with_metadata",
        )
        # Metadata/index lookup only. load_bars() can validate/checksum and
        # materialize millions of CSV rows, which is never acceptable here.
        latest = state.orchestrator.latest_bar_time(query)
        if latest is not None:
            last_bar_time = int(latest)
            data_lag_seconds = max(0, int((now_ms - last_bar_time) / 1000))
    except Exception:
        metadata_ok = False

    fetcher = getattr(state, "_fetcher", None)
    runner = getattr(state, "_live_runner", None)
    worker_status = worker_runtime_snapshot(state)
    fetcher_last = (
        getattr(fetcher, "last_fetch_at", None) if fetcher is not None else None
    )
    runner_alive = bool(
        (runner and getattr(runner, "_running", False))
        or worker_status.get("ready", worker_status["alive"])
    )
    status = "ok"
    if strategy.status == "error":
        status = "error"
    elif not metadata_ok:
        status = "metadata_error"
    elif data_lag_seconds is not None and data_lag_seconds > 20 * 60:
        status = "stale"
    elif strategy.enabled and not runner_alive:
        status = "runner_off"

    return {
        "status": status,
        "runner_alive": runner_alive,
        "last_bar_time": last_bar_time,
        "data_lag_seconds": data_lag_seconds,
        "metadata_ok": metadata_ok,
        "last_order": last_order,
        "last_fetch_at": fetcher_last,
    }


async def _strategy_health_async(
    state: GatewayState, strategy
) -> dict[str, object]:
    """Run bounded SQLite/manifest health reads outside the event loop."""

    return await asyncio.to_thread(_strategy_health, state, strategy)
