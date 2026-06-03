"""Strategy routes — CRUD, enable/disable, mode switching."""

from __future__ import annotations

import json
import time

import structlog
from fastapi import APIRouter, Depends, HTTPException

from openpine.gateway.deps import get_state, get_strategy_registry
from openpine.gateway.schemas import (
    CompareTvRequest,
    ReplayRequest,
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


# ── Replay ────────────────────────────────────────────────────────────────────


@router.post("/{strategy_id}/replay")
async def strategy_replay(
    strategy_id: str,
    state: GatewayState = Depends(get_state),
    registry: SQLiteStrategyRegistry = Depends(get_strategy_registry),
) -> dict[str, object]:
    """Replay a strategy over historical data (async backtest)."""
    import asyncio
    from openpine.gateway.ws_manager import ws_manager
    from openpine.gateway.schemas import ProgressUpdate

    try:
        s = registry.get_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")

    async def _run_replay():
        try:
            registry.update_status(strategy_id, "running")
            import time as _time_module
            from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
            from openpine.data.orchestrator import DataOrchestrator
            from openpine.runtime.engine import (
                BacktestEngineAdapter,
                BacktestRunConfig,
                load_strategy_class_from_artifact,
            )

            strategy_class, artifact = load_strategy_class_from_artifact(strategy_id)

            tf = parse_timeframe(s.timeframe)
            symbol = s.symbol or "BTCUSDT"
            account_id = getattr(s, "account_id", "default") or "default"
            key = InstrumentKey(
                exchange="binance", market_type="futures",
                symbol=symbol, price_type="last",
            )

            now_ms = int(_time_module.time() * 1000)
            start_ms = now_ms - 90 * 24 * 3600 * 1000
            end_ms = now_ms

            orchestrator = DataOrchestrator()
            bars = orchestrator.get_bars(BarQuery(
                instrument=key, timeframe=tf,
                start_ms=start_ms, end_ms=end_ms,
            ))

            run_id = f"replay_{strategy_id}_{int(_time_module.time() * 1000)}"
            config = BacktestRunConfig(
                run_id=run_id,
                strategy_id=strategy_id,
                account_id=account_id,
                artifact_path=artifact.path,
                capture_plots=True,
                initial_capital=10_000.0,
            )

            result = BacktestEngineAdapter().run(
                strategy_class, bars, config,
                params=getattr(artifact, "declaration_args", None),
            )

            registry.update_status(strategy_id, "paused")
            await ws_manager.broadcast(ProgressUpdate(
                operation_id=run_id,
                operation_type="replay",
                status="completed",
                pct=100.0,
                message=f"Replay done: {result.bars_processed} bars",
                detail={"run_id": run_id, "bars_processed": result.bars_processed},
            ))

        except Exception as exc:
            registry.update_status(strategy_id, "error")
            log.error("replay_failed", strategy_id=strategy_id, error=str(exc))
            await ws_manager.broadcast(ProgressUpdate(
                operation_id=f"replay_{strategy_id}",
                operation_type="replay",
                status="failed",
                message=str(exc),
            ))

    asyncio.create_task(_run_replay())
    return {
        "status": "started",
        "strategy_id": strategy_id,
        "message": "Replay started. Monitor progress via WebSocket /api/ws/events.",
    }


# ── Compare TV ────────────────────────────────────────────────────────────────


@router.post("/{strategy_id}/compare-tv")
async def strategy_compare_tv(
    strategy_id: str,
    req: "CompareTvRequest",
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Compare OpenPine plots against TradingView chart export."""
    import csv
    from pathlib import Path

    op_path = Path(req.openpine_plots_path)
    tv_path = Path(req.tv_chart_path)

    if not op_path.exists():
        raise HTTPException(400, f"OpenPine plots file not found: {op_path}")
    if not tv_path.exists():
        raise HTTPException(400, f"TradingView chart file not found: {tv_path}")

    exclude_cols = set()
    if not req.include_base_columns:
        exclude_cols = {"time", "bar_time", "bar_index", "open", "high", "low", "close", "volume", "Volume"}

    def _load_csv(path: Path) -> dict[int, dict[str, float]]:
        rows: dict[int, dict[str, float]] = {}
        with path.open(encoding="utf-8", newline="") as f:
            reader = csv.DictReader(f)
            time_col = "time" if "time" in (reader.fieldnames or []) else "bar_time"
            for row in reader:
                try:
                    ts = int(float(row.get(time_col, "0")))
                except (ValueError, TypeError):
                    continue
                parsed: dict[str, float] = {}
                for k, v in row.items():
                    if k in exclude_cols or k == time_col:
                        continue
                    try:
                        parsed[k] = float(v)
                    except (ValueError, TypeError):
                        continue
                if parsed:
                    rows[ts] = parsed
        return rows

    try:
        tv_data = _load_csv(tv_path)
        op_data = _load_csv(op_path)
    except Exception as exc:
        raise HTTPException(400, f"CSV parse error: {exc}")

    common_times = sorted(set(tv_data.keys()) & set(op_data.keys()))
    if not common_times:
        return {"strategy_id": strategy_id, "status": "error", "message": "No matching timestamps"}

    total_cells = 0
    mismatch_cells = 0
    max_abs_delta = 0.0
    worst_col = ""

    for ts in common_times:
        tv_row = tv_data[ts]
        op_row = op_data[ts]
        for col in set(tv_row.keys()) & set(op_row.keys()):
            total_cells += 1
            delta = abs(tv_row[col] - op_row[col])
            if delta > req.abs_tol:
                mismatch_cells += 1
                if delta > max_abs_delta:
                    max_abs_delta = delta
                    worst_col = col

    status = "match" if mismatch_cells == 0 else "mismatch"
    classification = "exact" if mismatch_cells == 0 else f"{mismatch_cells}/{total_cells} mismatches"

    return {
        "strategy_id": strategy_id,
        "status": status,
        "classification": classification,
        "mismatch_cells": mismatch_cells,
        "total_cells": total_cells,
        "max_abs_delta": max_abs_delta,
        "worst_column": worst_col,
        "timestamps_compared": len(common_times),
    }


# ── Enable / Disable (dedicated endpoints) ────────────────────────────────────


@router.post("/{strategy_id}/enable")
async def strategy_enable(
    strategy_id: str,
    registry: SQLiteStrategyRegistry = Depends(get_strategy_registry),
) -> dict[str, str]:
    """Enable a strategy for auto-refresh and trading."""
    try:
        s = registry.get_strategy(strategy_id)
        registry.set_enabled(strategy_id, True)
        log.info("strategy_enabled", strategy_id=strategy_id)
        return {"strategy_id": strategy_id, "enabled": "true", "status": "ok"}
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")


@router.post("/{strategy_id}/disable")
async def strategy_disable(
    strategy_id: str,
    registry: SQLiteStrategyRegistry = Depends(get_strategy_registry),
) -> dict[str, str]:
    """Disable a strategy."""
    try:
        s = registry.get_strategy(strategy_id)
        registry.set_enabled(strategy_id, False)
        log.info("strategy_disabled", strategy_id=strategy_id)
        return {"strategy_id": strategy_id, "enabled": "false", "status": "ok"}
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
