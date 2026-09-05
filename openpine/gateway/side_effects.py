"""HTTP-side helpers for admission and job persistence."""

from __future__ import annotations

from typing import Any, cast

from fastapi import HTTPException

from openpine.admission import DeploymentAdmissionIdentity, admit_deployment
import structlog
from openpine.jobs.persist import JobV1Error
from openpine_contracts import AdmitError, SemanticProfile

log = structlog.get_logger(__name__)


def require_http_admit(state: object, mode: str) -> None:
    deployment = getattr(state, "admission_identity", None)
    if not isinstance(deployment, DeploymentAdmissionIdentity):
        raise HTTPException(503, "ADMISSION_IDENTITY_REQUIRED")
    try:
        admit_deployment(mode=mode, deployment=deployment)
    except AdmitError as exc:
        raise HTTPException(403, exc.message) from exc


def persist_gateway_job(state: object, **kwargs: Any) -> dict[str, Any]:
    store = getattr(state, "job_store", None)
    if store is None:
        raise HTTPException(503, "JOB_PERSISTENCE_REQUIRED")
    kind = kwargs.get("kind")
    profile = kwargs.pop("semantic_profile", None)
    if not profile and kind in {"backtest", "optimize", "parity"}:
        raise HTTPException(400, "semantic_profile required")
    if isinstance(profile, SemanticProfile):
        profile = profile.value
    refs = list(kwargs.get("input_artifact_refs") or [])
    if profile:
        token = f"semantic_profile:{profile}"
        if token not in refs:
            refs.append(token)
    kwargs["input_artifact_refs"] = refs
    try:
        return cast(dict[str, Any], store.create(**kwargs))
    except JobV1Error as exc:
        log.error(
            "job_v1_persist_failed", error=str(exc), job_id=kwargs.get("job_id")
        )
        raise HTTPException(503, "JOB_PERSISTENCE_FAILED") from exc
