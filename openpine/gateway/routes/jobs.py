"""Persisted job inbox API. Dashboard snapshot is not the source of truth."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from openpine.gateway.deps import GatewayState, get_state

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _job_href(kind: str, job_id: str) -> str:
    if kind == "backtest":
        return f"/backtests/{job_id}"
    if kind == "optimizer":
        return f"/optimize/{job_id}"
    return f"/jobs/{job_id}"


def _from_scheduler_job(job: Any) -> dict[str, Any]:
    kind = getattr(job.job_type, "value", str(job.job_type))
    job_id = str(job.id)
    if not job_id:
        raise HTTPException(500, "persisted job is missing id")
    return {
        "job_id": job_id,
        "kind": kind,
        "state": getattr(job.status, "value", str(job.status)),
        "strategy_id": job.strategy_id,
        "created_at_utc_ms": job.created_at,
        "updated_at_utc_ms": job.updated_at,
        "started_at_utc_ms": job.started_at,
        "finished_at_utc_ms": job.finished_at,
        "error": job.error,
        "href": _job_href(kind, job_id),
    }


def _from_backtest_row(row: tuple[Any, ...]) -> dict[str, Any]:
    run_id, strategy_id, status, started_at, finished_at, created_at, error = row
    job_id = str(run_id or "")
    if not job_id:
        raise HTTPException(500, "persisted job is missing id")
    return {
        "job_id": job_id,
        "kind": "backtest",
        "state": str(status or "pending"),
        "strategy_id": strategy_id,
        "created_at_utc_ms": created_at or started_at,
        "updated_at_utc_ms": finished_at or started_at or created_at,
        "started_at_utc_ms": started_at,
        "finished_at_utc_ms": finished_at,
        "error": error,
        "href": _job_href("backtest", job_id),
    }


def _persistent_backtest_jobs(state: GatewayState) -> list[dict[str, Any]]:
    try:
        rows = state.storage.execute(
            """
            SELECT run_id, strategy_id, status, started_at, finished_at, created_at, error_message
            FROM backtest_runs
            ORDER BY COALESCE(created_at, started_at, 0) DESC
            LIMIT 100
            """
        ).fetchall()
    except Exception:
        return []
    return [_from_backtest_row(row) for row in rows]


def _all_jobs(state: GatewayState) -> list[dict[str, Any]]:
    scheduled = [_from_scheduler_job(job) for job in state.scheduler.list_jobs()]
    persisted = _persistent_backtest_jobs(state)
    by_id = {item["job_id"]: item for item in persisted + scheduled}
    jobs = list(by_id.values())
    jobs.sort(key=lambda item: int(item.get("updated_at_utc_ms") or 0), reverse=True)
    return jobs


@router.get("")
async def list_jobs(state: GatewayState = Depends(get_state)) -> dict[str, Any]:
    jobs = await asyncio.to_thread(_all_jobs, state)
    return {"jobs": jobs}


@router.get("/{job_id}")
async def get_job(job_id: str, state: GatewayState = Depends(get_state)) -> dict[str, Any]:
    if not job_id:
        raise HTTPException(400, "job_id is required")
    scheduled = state.scheduler.get_job(job_id)
    if scheduled is not None:
        return _from_scheduler_job(scheduled)
    for item in await asyncio.to_thread(_persistent_backtest_jobs, state):
        if item["job_id"] == job_id:
            return item
    raise HTTPException(404, f"Job not found: {job_id}")
