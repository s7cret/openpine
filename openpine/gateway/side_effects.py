"""HTTP-side helpers for admission and job persistence."""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException

from openpine.admission import DEFAULT_STACK_ID, admit_run
from openpine._compat import structlog
from openpine.jobs.persist import JobV1Error
from openpine_contracts import AdmitError, SemanticProfile

log = structlog.get_logger(__name__)


def require_http_admit(mode: str) -> None:
    try:
        admit_run(mode=mode, stack_id=DEFAULT_STACK_ID)
    except AdmitError as exc:
        raise HTTPException(403, exc.message) from exc


def persist_gateway_job(state: object, **kwargs: Any) -> dict[str, Any] | None:
    store = getattr(state, "job_store", None)
    if store is None:
        return None
    kind = kwargs.get("kind")
    profile = kwargs.pop("semantic_profile", None)
    if not profile:
        if kind in {"backtest", "optimize", "parity"}:
            log.warning(
                "job_v1_persist_missing_semantic_profile",
                error="semantic_profile required",
                job_id=kwargs.get("job_id"),
            )
            return None
        try:
            return store.create(**kwargs)
        except JobV1Error as exc:
            log.warning(
                "job_v1_persist_failed", error=str(exc), job_id=kwargs.get("job_id")
            )
            return None
    if isinstance(profile, SemanticProfile):
        profile = profile.value
    refs = list(kwargs.get("input_artifact_refs") or [])
    token = f"semantic_profile:{profile}"
    if token not in refs:
        refs.append(token)
    kwargs["input_artifact_refs"] = refs
    try:
        return store.create(**kwargs)
    except JobV1Error as exc:
        log.warning(
            "job_v1_persist_failed", error=str(exc), job_id=kwargs.get("job_id")
        )
        return None
