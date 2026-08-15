"""Persisted job.v1 HTTP API. Dashboard snapshot is not the owner."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query

from openpine.compare import compare_semantic_profiles, profile_from_job
from openpine.gateway.deps import GatewayState, get_state
from openpine.jobs.persist import JobV1Error

router = APIRouter(prefix="/jobs", tags=["jobs"])


@router.get("/compare")
def compare_jobs(
    left: str,
    right: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    try:
        left_job = state.job_store.get(left)
        right_job = state.job_store.get(right)
    except JobV1Error as exc:
        raise HTTPException(404, str(exc)) from exc
    result = compare_semantic_profiles(
        profile_from_job(left_job), profile_from_job(right_job)
    )
    result["left_job_id"] = left
    result["right_job_id"] = right
    return result


@router.get("")
def list_jobs(
    state: GatewayState = Depends(get_state),
    cursor: str | None = None,
    kind: str | None = None,
    job_state: Annotated[str | None, Query(alias="state")] = None,
    limit: int = 50,
) -> dict[str, object]:
    return state.job_store.list_jobs(
        kind=kind, state=job_state, cursor=cursor, limit=limit
    )


@router.get("/events")
def list_job_events(
    state: GatewayState = Depends(get_state),
    after: str = "",
) -> dict[str, object]:
    if state.job_store.needs_resync(after=after):
        return {"items": state.job_store.events(after=""), "resync": True}
    return {"items": state.job_store.events(after=after), "resync": False}


@router.get("/{job_id}")
def get_job(job_id: str, state: GatewayState = Depends(get_state)) -> dict[str, object]:
    try:
        return state.job_store.get(job_id)
    except JobV1Error as exc:
        raise HTTPException(404, str(exc)) from exc


@router.post("/{job_id}/cancel")
def cancel_job(
    job_id: str,
    state: GatewayState = Depends(get_state),
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> dict[str, object]:
    try:
        return state.job_store.cancel(job_id, idempotency_key=idempotency_key)
    except JobV1Error as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post("/{job_id}/retry")
def retry_job(
    job_id: str, state: GatewayState = Depends(get_state)
) -> dict[str, object]:
    try:
        return state.job_store.retry(job_id)
    except JobV1Error as exc:
        raise HTTPException(409, str(exc)) from exc
