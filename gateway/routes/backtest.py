"""Backtest routes — run, progress, results."""

from __future__ import annotations

import time
import uuid
from datetime import datetime, timezone

import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.schemas import (
    BacktestProgress,
    BacktestRunDetail,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestTradeResponse,
)
from openpine.gateway.ws_manager import ws_manager

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/backtest", tags=["backtest"])


def _parse_date_ms(value: str) -> int:
    """Parse ISO date or ms timestamp."""
    if value.isdigit():
        raw = int(value)
        return raw if raw > 10_000_000_000 else raw * 1000
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return int(parsed.timestamp() * 1000)


async def _run_backtest_background(
    state: GatewayState,
    strategy_id: str,
    run_id: str,
    from_ms: int,
    to_ms: int,
    params_override: dict | None,
    warmup_bars: int,
    capture_plots: bool,
) -> None:
    """Execute backtest in background, update progress via WebSocket."""
    try:
        ws_manager.update_progress(run_id, "backtest", "running", 0.0, "Loading strategy...")

        registry = state.strategy_registry
        try:
            strategy = registry.get_strategy(strategy_id)
        except KeyError:
            ws_manager.update_progress(run_id, "backtest", "failed", 0.0, "Strategy not found")
            await ws_manager.broadcast_progress(run_id)
            return

        # Load artifact
        ws_manager.update_progress(run_id, "backtest", "running", 0.1, "Loading artifact...")
        await ws_manager.broadcast_progress(run_id)

        from openpine.runtime.engine import load_strategy_class_from_artifact, BacktestArtifactError

        try:
            strategy_class = load_strategy_class_from_artifact(
                strategy.pine_id,
                strategy.artifact_id,
                symbol=strategy.symbol,
                timeframe=strategy.timeframe,
            )
        except BacktestArtifactError as exc:
            ws_manager.update_progress(run_id, "backtest", "failed", 0.1, str(exc))
            await ws_manager.broadcast_progress(run_id)
            state.backtest_store.mark_failed(run_id, str(exc))
            return

        # Load bars
        ws_manager.update_progress(run_id, "backtest", "running", 0.2, "Loading market data...")
        await ws_manager.broadcast_progress(run_id)

        from openpine.data.orchestrator import DataOrchestrator
        from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

        orchestrator = DataOrchestrator()
        tf = parse_timeframe(strategy.timeframe)
        query = BarQuery(
            instrument=InstrumentKey(
                exchange=strategy.exchange.lower(),
                market=strategy.market_type.lower(),
                symbol=strategy.symbol.upper(),
                price_type="trade",
            ),
            timeframe=tf,
            start_ms=from_ms,
            end_ms=to_ms,
            source="auto",
            gap_policy="fail_closed",
        )
        try:
            series = orchestrator.load_bars(query)
            bars = list(series.bars)
        except Exception as exc:
            ws_manager.update_progress(run_id, "backtest", "failed", 0.2, f"Data load failed: {exc}")
            await ws_manager.broadcast_progress(run_id)
            state.backtest_store.mark_failed(run_id, f"Data load failed: {exc}")
            return

        if not bars:
            ws_manager.update_progress(run_id, "backtest", "failed", 0.2, "No bars found")
            await ws_manager.broadcast_progress(run_id)
            state.backtest_store.mark_failed(run_id, "No bars found in range")
            return

        total_bars = len(bars)
        ws_manager.update_progress(
            run_id, "backtest", "running", 0.3,
            f"Running backtest on {total_bars} bars...",
        )
        await ws_manager.broadcast_progress(run_id)

        # Build config
        from openpine.runtime.engine import BacktestRunConfig

        params = {}
        if params_override:
            params = params_override
        elif strategy.params_json:
            import json
            params = json.loads(strategy.params_json)

        config = BacktestRunConfig(
            symbol=strategy.symbol,
            timeframe=strategy.timeframe,
            start_time=from_ms,
            end_time=to_ms,
            exchange=strategy.exchange,
            market_type=strategy.market_type,
            export_resume_state=False,
            content_hash_enabled=True,
            collect_events=True,
            collect_order_lifecycle=True,
            capture_plots=capture_plots,
        )

        # Run
        from openpine.runtime.engine import BacktestEngineAdapter

        adapter = BacktestEngineAdapter()

        def progress_callback(done: int, total: int) -> None:
            pct = 0.3 + 0.6 * (done / max(total, 1))
            ws_manager.update_progress(
                run_id, "backtest", "running", pct,
                f"Bars: {done}/{total}",
            )

        result = adapter.run(
            strategy_class,
            bars,
            config,
            params=params,
            progress_callback=progress_callback,
        )

        # Save results
        ws_manager.update_progress(run_id, "backtest", "running", 0.9, "Saving results...")
        await ws_manager.broadcast_progress(run_id)

        state.backtest_store.save_result(
            run_id=run_id,
            result=result.raw_result,
            trades=getattr(result.raw_result, "trades", []) or [],
            equity_curve=getattr(result.raw_result, "equity_curve", None),
        )

        ws_manager.update_progress(
            run_id, "backtest", "completed", 1.0,
            f"Done. {result.bars_processed} bars processed.",
        )
        await ws_manager.broadcast_progress(run_id)
        log.info("backtest_completed", run_id=run_id, bars=result.bars_processed)

    except Exception as exc:
        log.error("backtest_failed", run_id=run_id, error=str(exc))
        ws_manager.update_progress(run_id, "backtest", "failed", 0.0, str(exc))
        await ws_manager.broadcast_progress(run_id)
        try:
            state.backtest_store.mark_failed(run_id, str(exc))
        except Exception:
            pass


@router.post("/run", response_model=BacktestRunResponse)
async def run_backtest(
    body: BacktestRunRequest,
    background_tasks: BackgroundTasks,
    state: GatewayState = Depends(get_state),
) -> BacktestRunResponse:
    """Start a backtest run (async, tracks progress via WebSocket)."""
    from_ms = _parse_date_ms(body.from_time)
    to_ms = _parse_date_ms(body.to_time)
    if from_ms >= to_ms:
        raise HTTPException(400, "from_time must be before to_time")

    registry = state.strategy_registry
    try:
        strategy = registry.get_strategy(body.strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {body.strategy_id}")

    if not strategy.pine_id or not strategy.artifact_id:
        raise HTTPException(400, "Strategy has no pine_id or artifact_id. Compile first.")

    from openpine.storage.backtest_dto import BacktestRunRequest as BTRequest

    run_id = state.backtest_store.create_run(
        BTRequest(
            strategy_id=body.strategy_id,
            pine_id=strategy.pine_id,
            artifact_id=strategy.artifact_id,
            params_hash=strategy.params_hash,
            exchange=strategy.exchange,
            market_type=strategy.market_type,
            symbol=strategy.symbol,
            price_type="trade",
            timeframe=strategy.timeframe,
            from_time=from_ms,
            to_time=to_ms,
            warmup_bars=body.warmup_bars,
        )
    )

    ws_manager.update_progress(run_id, "backtest", "queued", 0.0, "Backtest queued")
    background_tasks.add_task(
        _run_backtest_background,
        state, body.strategy_id, run_id, from_ms, to_ms,
        body.params_override, body.warmup_bars, body.capture_plots,
    )

    log.info("backtest_started", run_id=run_id, strategy_id=body.strategy_id)
    return BacktestRunResponse(
        run_id=run_id,
        strategy_id=body.strategy_id,
        status="queued",
        started_at=int(time.time() * 1000),
    )


@router.get("/runs", response_model=list[BacktestRunDetail])
async def list_runs(
    strategy_id: str | None = None,
    limit: int = 50,
    state: GatewayState = Depends(get_state),
) -> list[BacktestRunDetail]:
    """List backtest runs."""
    store = state.backtest_store
    if strategy_id:
        runs = store.list_runs(strategy_id, limit=limit)
    else:
        runs = store.list_all_runs(limit=limit)
    return [
        BacktestRunDetail(
            run_id=r.run_id,
            strategy_id=r.strategy_id,
            status=r.status,
            started_at=r.started_at,
            finished_at=r.finished_at,
            symbol=r.symbol,
            timeframe=r.timeframe,
            from_time=r.from_time,
            to_time=r.to_time,
        )
        for r in runs
    ]


@router.get("/runs/{run_id}", response_model=BacktestRunDetail)
async def get_run(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> BacktestRunDetail:
    """Get details of a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")
    metrics = None
    try:
        metrics = state.backtest_store.get_metrics(run_id)
    except Exception:
        pass
    return BacktestRunDetail(
        run_id=run.run_id,
        strategy_id=run.strategy_id,
        status=run.status,
        started_at=run.started_at,
        finished_at=run.finished_at,
        symbol=run.symbol,
        timeframe=run.timeframe,
        from_time=run.from_time,
        to_time=run.to_time,
        metrics=metrics,
    )


@router.get("/runs/{run_id}/trades", response_model=list[BacktestTradeResponse])
async def get_run_trades(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> list[BacktestTradeResponse]:
    """Get trade log for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")
    trades = state.backtest_store.list_trades(run_id)
    return [
        BacktestTradeResponse(
            trade_id=t.trade_id,
            run_id=run_id,
            bar_index=t.bar_index,
            entry_time=t.entry_time,
            exit_time=t.exit_time,
            direction=t.direction,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            qty=t.qty,
            net_profit=t.net_profit,
            max_runup=t.max_runup,
            max_drawdown=t.max_drawdown,
        )
        for t in trades
    ]


@router.get("/progress/{run_id}", response_model=BacktestProgress | None)
async def get_progress(run_id: str) -> BacktestProgress | None:
    """Get current progress of a backtest run."""
    p = ws_manager.get_progress(run_id)
    if p is None:
        return None
    return BacktestProgress(
        run_id=p["operation_id"],
        status=p["status"],
        bars_processed=int(p.get("detail", {}).get("bars_processed", 0)),
        total_bars=int(p.get("detail", {}).get("total_bars", 0)),
        pct=p["pct"],
    )


@router.get("/progress/{run_id}/detail")
async def get_progress_detail(run_id: str) -> dict[str, object] | None:
    """Get detailed progress including error message."""
    return ws_manager.get_progress(run_id)
