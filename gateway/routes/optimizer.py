"""Optimizer routes — dry-run validation."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException

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
    try:
        from openpine.optimizer import OptimizerService
        result = OptimizerService().validate_config(
            strategy_id=req.strategy_id,
            trials=req.trials,
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
