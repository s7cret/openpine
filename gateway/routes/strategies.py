"""Strategy routes — CRUD, enable/disable, mode switching."""

from __future__ import annotations

import json
import time

import structlog
from fastapi import APIRouter, Depends, HTTPException

from openpine.gateway.deps import get_state, get_strategy_registry
from openpine.gateway.schemas import (
    StrategyCreate,
    StrategyResponse,
    StrategyUpdate,
)
from openpine.gateway.deps import GatewayState
from openpine.registry.strategies import SQLiteStrategyRegistry

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/strategies", tags=["strategies"])


def _to_response(s) -> StrategyResponse:
    return StrategyResponse(
        strategy_id=s.strategy_id,
        name=s.name,
        pine_id=s.pine_id,
        artifact_id=s.artifact_id,
        symbol=s.symbol,
        timeframe=s.timeframe,
        exchange=s.exchange,
        market_type=s.market_type,
        params_json=s.params_json,
        params_hash=s.params_hash,
        mode=s.mode,
        enabled=s.enabled,
        status=s.status,
        created_at=s.created_at,
        updated_at=s.updated_at,
    )


@router.get("", response_model=list[StrategyResponse])
async def list_strategies(
    registry: SQLiteStrategyRegistry = Depends(get_strategy_registry),
) -> list[StrategyResponse]:
    """List all strategies."""
    return [_to_response(s) for s in registry.list_strategies()]


@router.post("", response_model=StrategyResponse, status_code=201)
async def create_strategy(
    body: StrategyCreate,
    state: GatewayState = Depends(get_state),
) -> StrategyResponse:
    """Create a new strategy instance."""
    registry = state.strategy_registry

    # Validate Pine source exists
    try:
        pine_src = state.pine_registry.get_source(body.pine_id)
    except KeyError:
        raise HTTPException(400, f"Pine source not found: {body.pine_id}")

    # Validate artifact exists
    try:
        artifact = state.artifact_store.get_artifact(body.artifact_id, body.pine_id)
    except FileNotFoundError:
        raise HTTPException(400, f"Artifact not found: {body.artifact_id}")

    # Check compile status
    compile_meta = artifact.get("compile_meta", {})
    if compile_meta.get("compile_status") != "OK":
        raise HTTPException(
            400,
            f"Artifact {body.artifact_id} is not a successful compile "
            f"(status={compile_meta.get('compile_status')!r}). Recompile first.",
        )

    import hashlib
    params_hash = hashlib.sha256(body.params_json.encode()).hexdigest()[:16]

    strategy = registry.create_strategy(
        name=body.name,
        pine_id=body.pine_id,
        artifact_id=body.artifact_id,
        symbol=body.symbol,
        timeframe=body.timeframe,
        exchange=body.exchange,
        market_type=body.market_type,
        params_json=body.params_json,
        params_hash=params_hash,
        mode=body.mode.value if hasattr(body.mode, "value") else body.mode,
    )
    log.info("strategy_created", strategy_id=strategy.strategy_id, name=body.name)
    return _to_response(strategy)


@router.get("/{strategy_id}", response_model=StrategyResponse)
async def get_strategy(
    strategy_id: str,
    registry: SQLiteStrategyRegistry = Depends(get_strategy_registry),
) -> StrategyResponse:
    """Get a strategy by id."""
    try:
        s = registry.get_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
    return _to_response(s)


@router.patch("/{strategy_id}", response_model=StrategyResponse)
async def update_strategy(
    strategy_id: str,
    body: StrategyUpdate,
    state: GatewayState = Depends(get_state),
) -> StrategyResponse:
    """Update a strategy (partial)."""
    registry = state.strategy_registry
    try:
        s = registry.get_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")

    updates = body.model_dump(exclude_unset=True)
    if not updates:
        return _to_response(s)

    # Mode/enabled/status changes need special handling
    if "enabled" in updates:
        registry.set_enabled(strategy_id, updates["enabled"])
    if "mode" in updates:
        mode_val = updates["mode"]
        if hasattr(mode_val, "value"):
            mode_val = mode_val.value
        registry.update_mode(strategy_id, mode_val)
    if "status" in updates:
        registry.update_status(strategy_id, updates["status"])

    # Simple field updates via direct SQL
    simple_fields = {}
    for field in ("name", "symbol", "timeframe", "exchange", "market_type", "params_json"):
        if field in updates:
            simple_fields[field] = updates[field]
    if simple_fields:
        if "params_json" in simple_fields:
            import hashlib
            simple_fields["params_hash"] = hashlib.sha256(
                simple_fields["params_json"].encode()
            ).hexdigest()[:16]
        simple_fields["updated_at"] = int(time.time() * 1000)
        set_clause = ", ".join(f"{k}=?" for k in simple_fields)
        values = list(simple_fields.values()) + [strategy_id]
        conn = registry._conn
        conn.execute(
            f"UPDATE strategy_instances SET {set_clause} WHERE strategy_id=?",
            tuple(values),
        )
        conn.commit()

    s = registry.get_strategy(strategy_id)
    return _to_response(s)


@router.post("/{strategy_id}/action")
async def strategy_action(
    strategy_id: str,
    state: GatewayState = Depends(get_state),
    action: str = "pause",
) -> dict[str, str]:
    """Execute an action on a strategy: start, stop, pause, enable, clear_error."""
    registry = state.strategy_registry
    try:
        s = registry.get_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")

    if action == "start":
        if s.status == "error":
            raise HTTPException(400, "Cannot start strategy in error state. Clear error first.")
        registry.update_status(strategy_id, "running")
        registry.set_enabled(strategy_id, True)
    elif action == "stop" or action == "pause":
        registry.update_status(strategy_id, "paused")
        registry.set_enabled(strategy_id, False)
    elif action == "enable":
        registry.set_enabled(strategy_id, True)
    elif action == "clear_error":
        if s.status != "error":
            raise HTTPException(400, f"Strategy is not in error state (current: {s.status})")
        registry.update_status(strategy_id, "paused")
    else:
        raise HTTPException(400, f"Unknown action: {action}")

    log.info("strategy_action", strategy_id=strategy_id, action=action)
    return {"strategy_id": strategy_id, "action": action, "status": "ok"}


@router.delete("/{strategy_id}", status_code=204)
async def delete_strategy(
    strategy_id: str,
    registry: SQLiteStrategyRegistry = Depends(get_strategy_registry),
) -> None:
    """Delete a strategy."""
    try:
        registry.delete_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
    log.info("strategy_deleted", strategy_id=strategy_id)
