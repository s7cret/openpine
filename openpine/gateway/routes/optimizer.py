"""Optimizer routes — dry-run validation."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException

from openpine._compat import structlog
from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.schemas import (
    OptimizerDryRunRequest,
    OptimizerDryRunResponse,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/optimizer", tags=["optimizer"])


@router.post("/dry-run", response_model=OptimizerDryRunResponse)
async def optimizer_dry_run(
    req: OptimizerDryRunRequest,
    state: GatewayState = Depends(get_state),
) -> OptimizerDryRunResponse:
    """Validate optimizer configuration without launching optimization."""
    from openpine.gateway.side_effects import persist_gateway_job, require_http_admit

    require_http_admit("optimize")
    from openpine.admission import admit_semantic_profile
    from openpine_contracts import AdmitError

    strategy = None
    registry = getattr(state, "strategy_registry", None)
    if registry is not None:
        getter = getattr(registry, "get_strategy", None)
        if getter is not None:
            try:
                strategy = getter(req.strategy_id)
            except KeyError:
                strategy = None
    try:
        admitted = admit_semantic_profile(
            profile=getattr(req, "semantic_profile", None)
            or getattr(strategy, "semantic_profile", None),
            source="backtest",
            allow_legacy=bool(getattr(req, "allow_legacy", False)),
        )
    except AdmitError as exc:
        raise HTTPException(403, exc.message) from exc
    try:
        from openpine.optimizer import OptimizerService

        result = OptimizerService().validate_config(
            strategy_id=req.strategy_id,
            trials=req.trials,
        )
        persist_gateway_job(
            state,
            job_id=f"opt-dry-{req.strategy_id}",
            kind="optimize",
            actor="gateway",
            idempotency_key=f"opt-dry-{req.strategy_id}",
            semantic_profile=admitted.value,
        )
        return OptimizerDryRunResponse(
            strategy_id=result.strategy_id,
            trials_requested=result.trials_requested,
            status=result.status,
            reason=getattr(result, "reason", None),
        )
    except Exception as exc:
        log.error("optimizer_dry_run_failed", error=str(exc))
        raise HTTPException(500, f"Optimizer validation failed: {exc}")
