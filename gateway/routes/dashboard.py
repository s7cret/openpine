"""Dashboard routes — aggregated system overview."""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends

from openpine.gateway.deps import (
    GatewayState,
    get_state,
)
from openpine.gateway.schemas import (
    DashboardResponse,
    JobSummary,
    StrategySummary,
)
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
    strategies = []
    for s in registry.list_strategies():
        health = _strategy_health(state, s)
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

    # Jobs
    all_jobs = scheduler.list_jobs()
    jobs = JobSummary(
        pending=sum(1 for j in all_jobs if j.status == JobStatus.PENDING),
        running=sum(1 for j in all_jobs if j.status == JobStatus.RUNNING),
        done=sum(1 for j in all_jobs if j.status == JobStatus.DONE),
        failed=sum(1 for j in all_jobs if j.status == JobStatus.FAILED),
        recent=[
            {
                "id": j.id,
                "type": j.job_type.value,
                "status": j.status.value,
                "strategy_id": j.strategy_id,
                "created_at": j.created_at,
                "started_at": j.started_at,
                "finished_at": j.finished_at,
                "error": j.error,
            }
            for j in sorted(all_jobs, key=lambda x: x.created_at, reverse=True)[:20]
        ],
    )

    # Last event (support both old schema: created_at and new schema: timestamp_ms)
    last_event_ts = None
    try:
        cols = {r[1] for r in state.storage.execute("PRAGMA table_info(events)").fetchall()}
        ts_col = "timestamp_ms" if "timestamp_ms" in cols else "created_at"
        rows = state.storage.execute(
            f"SELECT MAX({ts_col}) FROM events"
        ).fetchone()
        if rows and rows[0]:
            last_event_ts = rows[0]
    except Exception:
        pass

    # Last bar update from PeriodicBarFetcher or storage
    last_bar_update_ts = None
    fetcher = getattr(state, '_fetcher', None)
    if fetcher is not None and fetcher.last_fetch_at is not None:
        last_bar_update_ts = fetcher.last_fetch_at
    else:
        # Fallback: check latest 1m bar timestamp from storage (fetcher stores at 1m)
        try:
            enabled = [s for s in registry.list_strategies() if s.enabled]
            if enabled:
                from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
                seen_symbols: set[str] = set()
                latest_ts = 0
                for s in enabled[:5]:
                    sym = s.symbol.upper()
                    if sym in seen_symbols:
                        continue
                    seen_symbols.add(sym)
                    try:
                        tf = parse_timeframe('1m')  # fetcher stores at 1m
                        key = InstrumentKey(exchange=s.exchange.lower(), market=s.market_type.lower(), symbol=sym)
                        now_ms = int(time.time() * 1000)
                        start_ms = now_ms - 3600000 * 24
                        query = BarQuery(instrument=key, timeframe=tf, start_ms=start_ms, end_ms=now_ms, source='storage', gap_policy='allow_with_metadata')
                        bar_time = state.orchestrator.latest_bar_time(query)
                        if bar_time is not None:
                            latest_ts = max(latest_ts, bar_time)
                    except Exception:
                        continue
                if latest_ts > 0:
                    last_bar_update_ts = latest_ts
        except Exception:
            pass

    return DashboardResponse(
        strategies=strategies,
        jobs=jobs,
        kill_switch=state._risk_kill_switch[0],
        uptime_seconds=time.time() - state._startup_time,
        last_event_time=last_event_ts,
        last_bar_update=last_bar_update_ts,
    )


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
    try:
        from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

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
        series = state.orchestrator.load_bars(query)
        if series.bars:
            last_bar_time = max(int(bar.time) for bar in series.bars)
            data_lag_seconds = max(0, int((now_ms - last_bar_time) / 1000))
    except Exception:
        pass

    fetcher = getattr(state, "_fetcher", None)
    runner = getattr(state, "_live_runner", None)
    background_worker = getattr(state, "_background_worker_process", None)
    fetcher_last = getattr(fetcher, "last_fetch_at", None) if fetcher is not None else None
    runner_alive = bool(
        (runner and getattr(runner, "_running", False))
        or (background_worker and background_worker.is_alive())
    )
    status = "ok"
    if strategy.status == "error":
        status = "error"
    elif data_lag_seconds is not None and data_lag_seconds > 20 * 60:
        status = "stale"
    elif strategy.enabled and not runner_alive:
        status = "runner_off"

    return {
        "status": status,
        "runner_alive": runner_alive,
        "last_bar_time": last_bar_time,
        "data_lag_seconds": data_lag_seconds,
        "last_order": last_order,
        "last_fetch_at": fetcher_last,
    }
