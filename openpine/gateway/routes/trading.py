"""Paper and live trading routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from openpine._compat import structlog
from openpine.admission import DEFAULT_STACK_ID, admit_run, admit_semantic_profile
from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.routes.activation_guard import (
    guarded_strategy_activation,
)
from openpine.gateway.schemas import (
    LiveStartRequest,
    PaperStartRequest,
    TradingStatusResponse,
)
from openpine.live_preview import (
    LiveConfirmError,
    make_live_preview,
    require_live_confirmation,
)
from openpine.registry.strategies import (
    ArchivedStrategyActivationError,
    WorkerCircuitOpenError,
)
from openpine_contracts import AdmitError

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


def _require_semantic_profile(*, profile: object | None, source: str, allow_legacy: bool = False):
    try:
        return admit_semantic_profile(profile=profile, source=source, allow_legacy=allow_legacy)
    except AdmitError as exc:
        raise HTTPException(403, exc.message) from exc


def _stamp_strategy_profile(strategy: Any, admitted: Any, registry: Any | None = None) -> None:
    if strategy is None or admitted is None:
        return
    value = getattr(admitted, "value", admitted)
    setattr(strategy, "semantic_profile", value)
    persist = getattr(registry, "set_semantic_profile", None)
    if callable(persist) and getattr(strategy, "strategy_id", None):
        persist(strategy.strategy_id, value)


@router.post("/paper/start", response_model=TradingStatusResponse)
async def start_paper(
    body: PaperStartRequest,
    state: GatewayState = Depends(get_state),
) -> TradingStatusResponse:
    """Start paper trading for a strategy."""
    try:
        admit_run(mode="paper", stack_id=DEFAULT_STACK_ID)
    except AdmitError as exc:
        raise HTTPException(403, exc.message) from exc
    admitted = _require_semantic_profile(
        profile=getattr(body, "semantic_profile", None),
        source="paper",
        allow_legacy=bool(getattr(body, "allow_legacy", False)),
    )
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

    _stamp_strategy_profile(s, admitted, registry)
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
    import time

    try:
        require_live_confirmation(
            strategy_id=body.strategy_id,
            preview_hash_value=body.preview_hash,
            confirmation=body.confirmation,
            expires_at_utc_ms=body.expires_at_utc_ms,
            now_ms=int(time.time() * 1000),
        )
    except LiveConfirmError as exc:
        raise HTTPException(400, str(exc)) from exc
    if not body.idempotency_key:
        raise HTTPException(400, "idempotency_key required")
    if not state.config.live_enabled:
        raise HTTPException(
            403,
            "Live trading is disabled globally. Enable in config before starting live.",
        )

    try:
        admit_run(mode="live", stack_id=DEFAULT_STACK_ID)
    except AdmitError as exc:
        raise HTTPException(403, exc.message) from exc
    admitted = _require_semantic_profile(
        profile=getattr(body, "semantic_profile", None),
        source="live",
        allow_legacy=bool(getattr(body, "allow_legacy", False)),
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

    _stamp_strategy_profile(s, admitted, registry)
    runner = getattr(state, "_live_runner", None)
    setter = getattr(runner, "set_strategy_htf_timeframe", None)
    if callable(setter):
        setter(body.strategy_id, getattr(body, "htf_timeframe", None))
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


@router.get("/live/admission")
async def live_admission() -> dict[str, object]:
    """Non-mutating live admission preview. Never starts trading."""
    from openpine.admission import DEFAULT_STACK_ID, admit_run
    from openpine_contracts import AdmitError

    try:
        result = admit_run(mode="live", stack_id=DEFAULT_STACK_ID)
    except AdmitError as exc:
        return {
            "admitted": False,
            "code": exc.code,
            "message": exc.message,
            "mutating": False,
        }
    return {**result.to_dict(), "mutating": False}


@router.get("/live/admission/preview")
async def live_admission_preview(
    strategy_id: str = Query(..., min_length=1),
) -> dict[str, object]:
    """Immutable start preview. Does not start trading."""
    import time

    return make_live_preview(strategy_id, now_ms=int(time.time() * 1000))


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
