"""Paper and live trading routes."""

from __future__ import annotations

import structlog
from fastapi import APIRouter, Depends, HTTPException

from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.schemas import (
    LiveStartRequest,
    PaperStartRequest,
    TradingStatusResponse,
)

log = structlog.get_logger(__name__)
router = APIRouter(tags=["trading"])


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

    registry.update_status(body.strategy_id, "running")
    registry.update_mode(body.strategy_id, "paper")
    registry.set_enabled(body.strategy_id, True)

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

    registry.update_status(body.strategy_id, "running")
    registry.update_mode(body.strategy_id, "live")
    registry.set_enabled(body.strategy_id, True)

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
            pos_data = snapshot.state_data.get("position", {})
            position_qty = pos_data.get("qty")
            position_side = pos_data.get("side")
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
