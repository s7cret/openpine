"""Paper and live trading routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from openpine._compat import structlog
from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.routes.activation_guard import (
    guarded_strategy_activation,
)
from openpine.gateway.schemas import (
    LiveStartRequest,
    PaperStartRequest,
    TradingStatusResponse,
)
from openpine.registry.strategies import (
    ArchivedStrategyActivationError,
    WorkerCircuitOpenError,
)

log = structlog.get_logger(__name__)
router = APIRouter(tags=["trading"])


def _activate_registry_strategy(
    registry: Any, strategy_id: str, *, status: str, mode: str
) -> None:
    activate = getattr(registry, "activate_strategy", None)
    if callable(activate):
        activate(strategy_id, status=status, mode=mode)
        return
    registry.update_status(strategy_id, status)
    registry.update_mode(strategy_id, mode)
    registry.set_enabled(strategy_id, True)


@router.post("/paper/start", response_model=TradingStatusResponse)
async def start_paper(
    body: PaperStartRequest,
    state: GatewayState = Depends(get_state),
) -> TradingStatusResponse:
    """Start paper trading for a strategy."""
    registry = state.strategy_registry
    try:
        s = registry.get_strategy(body.strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {body.strategy_id}")

    if s.status == "error":
        raise HTTPException(400, "Cannot start paper: strategy is in error state.")
    if getattr(s, "archived", False):
        raise HTTPException(400, "Archived strategy cannot be started")
    try:
        with guarded_strategy_activation(state):
            _activate_registry_strategy(
                registry, body.strategy_id, status="running", mode="paper"
            )
    except WorkerCircuitOpenError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ArchivedStrategyActivationError as exc:
        raise HTTPException(400, str(exc)) from exc

    log.info("paper_started", strategy_id=body.strategy_id)
    return TradingStatusResponse(
        strategy_id=body.strategy_id,
        mode="paper",
        status="running",
    )


@router.post("/paper/stop")
async def stop_paper(
    body: PaperStartRequest,
    state: GatewayState = Depends(get_state),
) -> dict[str, str]:
    """Stop paper trading for a strategy."""
    registry = state.strategy_registry
    try:
        registry.get_strategy(body.strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {body.strategy_id}")

    transition = getattr(registry, "transition_strategy", None)
    if callable(transition):
        transition(body.strategy_id, status="paused", enabled=False)
    else:
        registry.update_status(body.strategy_id, "paused")
        registry.set_enabled(body.strategy_id, False)

    log.info("paper_stopped", strategy_id=body.strategy_id)
    return {"strategy_id": body.strategy_id, "status": "stopped"}


@router.post("/live/start", response_model=TradingStatusResponse)
async def start_live(
    body: LiveStartRequest,
    state: GatewayState = Depends(get_state),
) -> TradingStatusResponse:
    """Start live trading for a strategy (requires global live_enabled)."""
    from openpine.stack_lock import StackLockAdmissionError, admit_stack_lock
    from fastapi import HTTPException

    try:
        admit_stack_lock(mode="LIVE")
    except StackLockAdmissionError as exc:
        raise HTTPException(409, f"stack lock admission failed: {exc}") from exc
    if not state.config.live_enabled:
        raise HTTPException(
            403,
            "Live trading is disabled globally. Enable in config before starting live.",
        )

    registry = state.strategy_registry
    try:
        s = registry.get_strategy(body.strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {body.strategy_id}")

    if s.status == "error":
        raise HTTPException(400, "Cannot start live: strategy is in error state.")
    if getattr(s, "archived", False):
        raise HTTPException(400, "Archived strategy cannot be started")
    try:
        with guarded_strategy_activation(state):
            _activate_registry_strategy(
                registry, body.strategy_id, status="running", mode="live"
            )
    except WorkerCircuitOpenError as exc:
        raise HTTPException(503, str(exc)) from exc
    except ArchivedStrategyActivationError as exc:
        raise HTTPException(400, str(exc)) from exc

    log.info("live_started", strategy_id=body.strategy_id)
    return TradingStatusResponse(
        strategy_id=body.strategy_id,
        mode="live",
        status="running",
    )


@router.post("/live/stop")
async def stop_live(
    body: LiveStartRequest,
    state: GatewayState = Depends(get_state),
) -> dict[str, str]:
    """Stop live trading for a strategy."""
    registry = state.strategy_registry
    try:
        registry.get_strategy(body.strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {body.strategy_id}")

    transition = getattr(registry, "transition_strategy", None)
    if callable(transition):
        transition(body.strategy_id, status="disabled", enabled=False)
    else:
        registry.update_status(body.strategy_id, "disabled")
        registry.set_enabled(body.strategy_id, False)

    log.info("live_stopped", strategy_id=body.strategy_id)
    return {"strategy_id": body.strategy_id, "status": "stopped"}


@router.get("/trading/status/{strategy_id}", response_model=TradingStatusResponse)
async def get_trading_status(
    strategy_id: str,
    state: GatewayState = Depends(get_state),
) -> TradingStatusResponse:
    """Get trading status for a strategy."""
    registry = state.strategy_registry
    try:
        s = registry.get_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")

    # Try to load latest state snapshot for position info
    position_qty = None
    position_side = None
    last_bar_time = None
    try:
        snapshot = state.state_store.load_snapshot(strategy_id)
        if snapshot:
            last_bar_time = snapshot.bar_time
            state_data = snapshot.state_data
            if isinstance(state_data, dict):
                pos_data = state_data.get("position", {})
                if isinstance(pos_data, dict):
                    position_qty = pos_data.get("qty")
                    position_side = pos_data.get("side")
            else:
                broker_state = getattr(state_data, "broker_state", None)
                position = getattr(broker_state, "position", None)
                if position is not None:
                    position_qty = getattr(position, "size", None)
                    position_side = getattr(position, "direction", None)
    except Exception:
        pass

    return TradingStatusResponse(
        strategy_id=strategy_id,
        mode=s.mode,
        status=s.status,
        last_bar_time=last_bar_time,
        position_qty=position_qty,
        position_side=position_side,
    )
