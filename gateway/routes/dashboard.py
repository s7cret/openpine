"""Dashboard routes — aggregated system overview."""

from __future__ import annotations

import time

import structlog
from fastapi import APIRouter, Depends

from openpine.gateway.deps import (
    GatewayState,
    get_event_bus,
    get_scheduler,
    get_state,
    get_strategy_registry,
)
from openpine.gateway.schemas import (
    DashboardResponse,
    JobSummary,
    StrategySummary,
)
from openpine.jobs import JobStatus

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("", response_model=DashboardResponse)
async def dashboard(
    state: GatewayState = Depends(get_state),
) -> DashboardResponse:
    """Aggregated system overview."""
    registry = state.strategy_registry
    scheduler = state.scheduler

    # Strategies
    strategies = []
    for s in registry.list_strategies():
        strategies.append(
            StrategySummary(
                strategy_id=s.strategy_id,
                name=s.name,
                symbol=s.symbol,
                timeframe=s.timeframe,
                mode=s.mode,
                status=s.status,
                enabled=s.enabled,
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

    # Last event
    last_event_ts = None
    try:
        rows = state.storage.execute(
            "SELECT MAX(timestamp_ms) FROM events"
        ).fetchone()
        if rows and rows[0]:
            last_event_ts = rows[0]
    except Exception:
        pass

    return DashboardResponse(
        strategies=strategies,
        jobs=jobs,
        kill_switch=state._risk_kill_switch[0],
        uptime_seconds=time.time() - state._startup_time,
        last_event_time=last_event_ts,
    )
