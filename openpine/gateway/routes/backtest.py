"""Backtest routes — run, progress, results."""

from __future__ import annotations

import time
import hashlib
import multiprocessing as mp
import queue
import traceback
from pathlib import Path

from openpine._compat import structlog
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException

from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.schemas import (
    BacktestEstimateResponse,
    BacktestProgress,
    BacktestRunDetail,
    BacktestRunRequest,
    BacktestRunResponse,
    BacktestTradeResponse,
)
from openpine.gateway.ws_manager import ws_manager
from openpine.timezones import parse_timestamp_ms

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/backtest", tags=["backtest"])


def _parse_date_ms(value: str) -> int:
    """Parse ISO date or ms timestamp using the configured default timezone."""
    return parse_timestamp_ms(value, 0)


def _market_data_query_for_strategy(strategy, from_ms: int, to_ms: int):
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

    return BarQuery(
        instrument=InstrumentKey(
            exchange=strategy.exchange.lower(),
            market=strategy.market_type.lower(),
            symbol=strategy.symbol.upper(),
        ),
        timeframe=parse_timeframe(strategy.timeframe),
        start_ms=from_ms,
        end_ms=to_ms,
        gap_policy="allow_with_metadata",
    )


def _estimate_backtest_market_data(
    strategy, from_ms: int, to_ms: int
) -> BacktestEstimateResponse:
    query = _market_data_query_for_strategy(strategy, from_ms, to_ms)
    duration_ms = query.timeframe.duration_ms or 60000
    estimated_bars = max(0, (query.end_ms - query.start_ms) // duration_ms + 1)
    estimated_pages = max(1, (estimated_bars + 999) // 1000)
    return BacktestEstimateResponse(
        strategy_id=strategy.strategy_id,
        symbol=strategy.symbol.upper(),
        timeframe=strategy.timeframe,
        exchange=strategy.exchange.lower(),
        market_type=strategy.market_type.lower(),
        requested_from=from_ms,
        requested_to=to_ms,
        effective_from=query.start_ms,
        effective_to=query.end_ms,
        earliest_available=None,
        adjusted=False,
        estimated_bars=estimated_bars,
        estimated_pages=estimated_pages,
    )


def _backtest_progress_source_label(phase: str, query) -> str:
    if phase.startswith("cache"):
        return "cache"
    return f"{query.instrument.exchange} {query.instrument.market}"


def _bar_series_fingerprint(series) -> str:
    digest = hashlib.sha256()
    digest.update(b"openpine.bar_series.v1\0")
    query = series.query
    digest.update(str(query.instrument.exchange).encode())
    digest.update(b"\0")
    digest.update(str(query.instrument.market).encode())
    digest.update(b"\0")
    digest.update(str(query.instrument.symbol).encode())
    digest.update(b"\0")
    digest.update(str(query.timeframe.canonical).encode())
    digest.update(b"\0")
    digest.update(str(query.start_ms).encode())
    digest.update(b"\0")
    digest.update(str(query.end_ms).encode())
    for bar in series.bars:
        digest.update(
            (
                f"{bar.time}|{bar.time_close}|{bar.open:.12g}|{bar.high:.12g}|"
                f"{bar.low:.12g}|{bar.close:.12g}|{bar.volume!r}\n"
            ).encode()
        )
    return digest.hexdigest()


def _backtest_process_entry(
    out, adapter, strategy_class, bars, config, params, runtime_data_provider, effective_pre_bars=None
):
    def progress(done: int, total: int) -> None:
        try:
            out.put_nowait(("progress", int(done), int(total)))
        except Exception:
            pass

    try:
        run_kwargs = {
            "params": params,
            "progress_callback": progress,
            "runtime_data_provider": runtime_data_provider,
        }
        if effective_pre_bars is not None:
            run_kwargs["effective_pre_bars"] = effective_pre_bars
        result = adapter.run(
            strategy_class,
            bars,
            config,
            **run_kwargs,
        )
        out.put(("ok", result))
    except BaseException as exc:
        out.put(("err", exc.__class__.__name__, str(exc), traceback.format_exc()))


def _run_backtest_in_process(
    adapter,
    strategy_class,
    bars,
    config,
    params,
    runtime_data_provider,
    progress_callback=None,
    effective_pre_bars=None,
):
    ctx = mp.get_context("fork")
    out = ctx.Queue()
    proc = ctx.Process(
        target=_backtest_process_entry,
        args=(
            out,
            adapter,
            strategy_class,
            bars,
            config,
            params,
            runtime_data_provider,
            effective_pre_bars,
        ),
    )
    proc.start()
    final: tuple | None = None
    while proc.is_alive() or final is None:
        try:
            status, *parts = out.get(timeout=0.5)
        except queue.Empty:
            if not proc.is_alive():
                break
            continue
        if status == "progress":
            if progress_callback is not None:
                progress_callback(int(parts[0]), int(parts[1]))
            continue
        final = (status, *parts)
        break
    proc.join()
    if final is None:
        while True:
            try:
                status, *parts = out.get_nowait()
            except queue.Empty:
                break
            if status == "progress":
                if progress_callback is not None:
                    progress_callback(int(parts[0]), int(parts[1]))
                continue
            final = (status, *parts)
            break
    try:
        if final is None:
            raise queue.Empty
        status, *parts = final
    except queue.Empty as exc:
        if proc.exitcode == 0:
            raise RuntimeError("backtest worker exited without a result") from exc
        raise RuntimeError(f"backtest worker exited with code {proc.exitcode}") from exc
    finally:
        out.close()
        out.cancel_join_thread()
    if status == "ok":
        return parts[0]
    exc_name, message, tb = parts
    raise RuntimeError(f"{exc_name}: {message}\n{tb}")


def _ensure_backtest_data_fingerprint_column(state: GatewayState) -> None:
    columns = {
        row[1]
        for row in state.storage.execute("PRAGMA table_info(backtest_runs)").fetchall()
    }
    if "data_fingerprint" in columns:
        return
    state.storage.execute("ALTER TABLE backtest_runs ADD COLUMN data_fingerprint TEXT")
    state.storage.execute(
        "CREATE INDEX IF NOT EXISTS idx_backtest_runs_data_fingerprint ON backtest_runs(data_fingerprint)"
    )
    state.storage.commit()


def _save_backtest_data_fingerprint(
    state: GatewayState, run_id: str, fingerprint: str
) -> None:
    _ensure_backtest_data_fingerprint_column(state)
    now = int(time.time() * 1000)
    state.storage.execute(
        "UPDATE backtest_runs SET data_fingerprint = ?, updated_at = ? WHERE run_id = ?",
        (fingerprint, now, run_id),
    )
    state.storage.commit()


def _normalize_metrics_payload(metrics: dict | None) -> dict | None:
    if not isinstance(metrics, dict):
        return metrics
    payload = (
        metrics.get("metrics") if isinstance(metrics.get("metrics"), dict) else metrics
    )
    normalized = dict(payload)
    if "trades_total" not in normalized and "total_trades" in normalized:
        normalized["trades_total"] = normalized["total_trades"]
    if "total_trades" not in normalized and "trades_total" in normalized:
        normalized["total_trades"] = normalized["trades_total"]
    return normalized


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
    import asyncio

    async def cancel_if_requested(phase: str) -> bool:
        if run_id not in state.backtest_cancel_requests:
            return False
        state.backtest_cancel_requests.discard(run_id)
        state.backtest_store.mark_cancelled(run_id, f"Cancelled during {phase}")
        ws_manager.update_progress(
            run_id, "backtest", "cancelled", 0.0, f"Cancelled during {phase}"
        )
        await ws_manager.broadcast_progress(run_id)
        return True

    try:
        ws_manager.update_progress(
            run_id, "backtest", "running", 0.0, "Loading strategy..."
        )

        registry = state.strategy_registry
        try:
            strategy = registry.get_strategy(strategy_id)
        except KeyError:
            ws_manager.update_progress(
                run_id, "backtest", "failed", 0.0, "Strategy not found"
            )
            await ws_manager.broadcast_progress(run_id)
            return

        # Load artifact
        if await cancel_if_requested("strategy load"):
            return
        ws_manager.update_progress(
            run_id, "backtest", "running", 0.1, "Loading artifact..."
        )
        await ws_manager.broadcast_progress(run_id)

        from openpine.runtime.engine import (
            load_strategy_class_from_artifact,
            BacktestArtifactError,
        )

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
        if await cancel_if_requested("artifact load"):
            return
        ws_manager.update_progress(
            run_id, "backtest", "running", 0.2, "Loading market data..."
        )
        await ws_manager.broadcast_progress(run_id)

        query = _market_data_query_for_strategy(strategy, from_ms, to_ms)
        estimate = _estimate_backtest_market_data(strategy, from_ms, to_ms)

        def bar_load_progress(
            bars_fetched: int,
            pages: int,
            total_bars: int | None = None,
            total_pages: int | None = None,
            earliest_open_ms: int | None = None,
            phase: str = "fetch",
        ) -> None:
            expected_bars = total_bars or estimate.estimated_bars
            expected_pages = total_pages or estimate.estimated_pages
            page_ratio = pages / max(expected_pages, 1)
            pct = 0.2 + 0.1 * max(0.0, min(page_ratio, 1.0))
            source = _backtest_progress_source_label(phase, query)
            ws_manager.update_progress(
                run_id,
                "backtest",
                "running",
                pct,
                f"Loading bars from {source}: {bars_fetched:,}/{expected_bars:,} bars "
                f"({pages}/{expected_pages} pages)",
                detail={
                    "phase": phase,
                    "bars_processed": bars_fetched,
                    "total_bars": expected_bars,
                    "pages_processed": pages,
                    "total_pages": expected_pages,
                    "requested_from": from_ms,
                    "effective_from": estimate.effective_from,
                    "earliest_available": earliest_open_ms,
                    "adjusted": estimate.adjusted,
                },
            )

        try:
            loop = asyncio.get_event_loop()
            series = await loop.run_in_executor(
                None,
                lambda: state.orchestrator.load_bars(
                    query, progress_callback=bar_load_progress
                ),
            )
            bars = list(series.bars)
        except Exception as exc:
            ws_manager.update_progress(
                run_id, "backtest", "failed", 0.2, f"Data load failed: {exc}"
            )
            await ws_manager.broadcast_progress(run_id)
            state.backtest_store.mark_failed(run_id, f"Data load failed: {exc}")
            return

        if not bars:
            ws_manager.update_progress(
                run_id, "backtest", "failed", 0.2, "No bars found"
            )
            await ws_manager.broadcast_progress(run_id)
            state.backtest_store.mark_failed(run_id, "No bars found in range")
            return

        if await cancel_if_requested("market data load"):
            return

        data_fingerprint = _bar_series_fingerprint(series)
        try:
            _save_backtest_data_fingerprint(state, run_id, data_fingerprint)
        except Exception as exc:
            log.warning(
                "backtest_data_fingerprint_save_failed", run_id=run_id, error=str(exc)
            )

        total_bars = len(bars)
        ws_manager.update_progress(
            run_id,
            "backtest",
            "running",
            0.3,
            f"Running backtest on {total_bars} bars...",
            detail={
                "bars_processed": 0,
                "total_bars": total_bars,
                "phase": "compute",
                "data_fingerprint": data_fingerprint,
            },
        )
        await ws_manager.broadcast_progress(run_id)

        # Build config — read declaration args from artifact
        if await cancel_if_requested("backtest setup"):
            return
        from openpine.runtime.engine import BacktestRunConfig

        # Read strategy declaration args (calc_on_order_fills, commission, etc.)
        try:
            artifact = state.artifact_store.get_artifact(
                strategy.artifact_id, strategy.pine_id
            )
            compile_meta = artifact.get("compile_meta", {})
            declaration = compile_meta.get("translation_metadata", {}).get(
                "declaration", {}
            )
            decl_args = declaration.get("arguments", {})
        except Exception:
            decl_args = {}

        params = {}
        if params_override:
            params = params_override
        elif strategy.params_json:
            import json

            params = json.loads(strategy.params_json)

        # Map commission_type aliases
        commission_type = {
            "cash_per_order": "fixed_per_order",
            "cash_per_contract": "fixed_per_contract",
        }.get(
            str(decl_args.get("commission_type", "none")),
            decl_args.get("commission_type", "none"),
        )

        config = BacktestRunConfig(
            symbol=strategy.symbol,
            timeframe=strategy.timeframe,
            start_time=from_ms,
            end_time=to_ms,
            exchange=strategy.exchange,
            market_type=strategy.market_type,
            initial_capital=decl_args.get("initial_capital", 10000.0),
            default_qty_type=decl_args.get("default_qty_type", "fixed"),
            default_qty_value=decl_args.get("default_qty_value", 1.0),
            commission_type=commission_type or "none",
            commission_value=decl_args.get("commission_value", 0.0),
            slippage=decl_args.get("slippage", 0.0),
            slippage_type=decl_args.get("slippage_type", "tick"),
            exit_matching=decl_args.get("close_entries_rule", "fifo").upper(),
            pyramiding=decl_args.get("pyramiding", 0),
            margin_long=decl_args.get("margin_long", 100.0),
            margin_short=decl_args.get("margin_short", 100.0),
            process_orders_on_close=bool(
                decl_args.get("process_orders_on_close", False)
            ),
            calc_on_order_fills=bool(decl_args.get("calc_on_order_fills", False)),
            calc_on_every_tick=bool(decl_args.get("calc_on_every_tick", False)),
            use_bar_magnifier=bool(decl_args.get("use_bar_magnifier", False)),
            export_resume_state=False,
            content_hash_enabled=True,
            collect_events=True,
            collect_order_lifecycle=True,
            capture_plots=capture_plots,
        )

        # Run
        from openpine.runtime.engine import BacktestEngineAdapter
        from openpine.data.provider_adapter import create_local_runtime_data_provider_adapter

        adapter = BacktestEngineAdapter()

        # Create runtime data provider for request.security support
        runtime_data_provider = None
        try:
            runtime_data_provider = create_local_runtime_data_provider_adapter(
                cache_dir=(state.config.data_cache_root or (state.config.data_dir / "cache")) / "marketdata",
                exchange=config.exchange,
                market=config.market_type,
                prefetch_end_ms=to_ms,
            )
        except Exception as exc:
            log.warning("runtime_data_provider_init_failed", error=str(exc))

        def progress_callback(done: int, total: int) -> None:
            pct = 0.3 + 0.6 * (done / max(total, 1))
            ws_manager.update_progress(
                run_id,
                "backtest",
                "running",
                pct,
                f"Bars: {done}/{total}",
                detail={
                    "bars_processed": done,
                    "total_bars": total,
                    "phase": "compute",
                },
            )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _run_backtest_in_process(
                adapter,
                strategy_class,
                bars,
                config,
                params,
                runtime_data_provider,
                progress_callback,
            ),
        )

        if await cancel_if_requested("compute"):
            return

        # Save results
        ws_manager.update_progress(
            run_id, "backtest", "running", 0.9, "Saving results..."
        )
        await ws_manager.broadcast_progress(run_id)

        state.backtest_store.save_result(
            run_id=run_id,
            result=result.raw_result,
            trades=getattr(result.raw_result, "trades", []) or [],
            equity_curve=getattr(result.raw_result, "equity_curve", None),
            plots=getattr(result.raw_result, "plots", None) if capture_plots else None,
        )

        ws_manager.update_progress(
            run_id,
            "backtest",
            "completed",
            1.0,
            f"Done. {result.bars_processed} bars processed.",
            detail={
                "bars_processed": result.bars_processed,
                "total_bars": result.bars_processed,
                "phase": "completed",
                "data_fingerprint": data_fingerprint,
            },
        )
        await ws_manager.broadcast_progress(run_id)
        log.info("backtest_completed", run_id=run_id, bars=result.bars_processed)
        state.backtest_cancel_requests.discard(run_id)

    except Exception as exc:
        log.error("backtest_failed", run_id=run_id, error=str(exc))
        ws_manager.update_progress(run_id, "backtest", "failed", 0.0, str(exc))
        await ws_manager.broadcast_progress(run_id)
        try:
            state.backtest_store.mark_failed(run_id, str(exc))
        except Exception:
            pass
        state.backtest_cancel_requests.discard(run_id)


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
        raise HTTPException(
            400, "Strategy has no pine_id or artifact_id. Compile first."
        )

    estimate = _estimate_backtest_market_data(strategy, from_ms, to_ms)
    if estimate.effective_from >= estimate.effective_to:
        raise HTTPException(400, "No listed market data found in selected range")

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
            from_time=estimate.effective_from,
            to_time=estimate.effective_to,
            warmup_bars=body.warmup_bars,
        )
    )

    queued_message = "Backtest queued"
    if estimate.adjusted:
        queued_message = (
            f"Backtest queued. Range adjusted to listed data: "
            f"{estimate.estimated_bars:,} bars ({estimate.estimated_pages} pages)."
        )
    ws_manager.update_progress(
        run_id,
        "backtest",
        "queued",
        0.0,
        queued_message,
        detail=estimate.model_dump(),
    )
    background_tasks.add_task(
        _run_backtest_background,
        state,
        body.strategy_id,
        run_id,
        estimate.effective_from,
        estimate.effective_to,
        body.params_override,
        body.warmup_bars,
        body.capture_plots,
    )

    log.info("backtest_started", run_id=run_id, strategy_id=body.strategy_id)
    return BacktestRunResponse(
        run_id=run_id,
        strategy_id=body.strategy_id,
        status="queued",
        started_at=int(time.time() * 1000),
    )


@router.get("/estimate", response_model=BacktestEstimateResponse)
async def estimate_backtest(
    strategy_id: str,
    from_time: str,
    to_time: str,
    state: GatewayState = Depends(get_state),
) -> BacktestEstimateResponse:
    """Estimate effective market data range, bars, and provider fetch pages."""
    from_ms = _parse_date_ms(from_time)
    to_ms = _parse_date_ms(to_time)
    if from_ms >= to_ms:
        raise HTTPException(400, "from_time must be before to_time")
    try:
        strategy = state.strategy_registry.get_strategy(strategy_id)
    except KeyError:
        raise HTTPException(404, f"Strategy not found: {strategy_id}")
    return _estimate_backtest_market_data(strategy, from_ms, to_ms)


@router.get("/runs", response_model=list[BacktestRunDetail])
async def list_runs(
    strategy_id: str | None = None,
    limit: int = 50,
    state: GatewayState = Depends(get_state),
) -> list[BacktestRunDetail]:
    """List backtest runs."""
    store = state.backtest_store
    registry = state.strategy_registry
    if strategy_id:
        runs = store.list_runs(strategy_id, limit=limit)
    else:
        runs = store.list_all_runs(limit=limit)

    # Compute version per strategy: count of prior runs + 1
    version_counter: dict[str, int] = {}
    run_versions: dict[str, int] = {}
    for r in sorted(runs, key=lambda x: x.started_at or 0):
        version_counter[r.strategy_id] = version_counter.get(r.strategy_id, 0) + 1
        run_versions[r.run_id] = version_counter[r.strategy_id]

    result = []
    for r in runs:
        # Look up strategy name
        strategy_name = None
        try:
            strat = registry.get_strategy(r.strategy_id)
            strategy_name = strat.name
        except (KeyError, Exception):
            pass
        metrics = None
        try:
            metrics = _normalize_metrics_payload(store.get_metrics(r.run_id))
        except Exception:
            pass

        result.append(
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
                bars_processed=getattr(r, "bars_processed", None),
                metrics=metrics,
                strategy_name=strategy_name,
                version=run_versions.get(r.run_id),
            )
        )
    return result


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
        metrics = _normalize_metrics_payload(state.backtest_store.get_metrics(run_id))
    except Exception:
        pass

    strategy_name = None
    try:
        strat = state.strategy_registry.get_strategy(run.strategy_id)
        strategy_name = strat.name
    except (KeyError, Exception):
        pass

    # Compute version: count of runs for this strategy up to and including this one
    all_runs = state.backtest_store.list_runs(run.strategy_id, limit=1000)
    version = 1
    for i, r in enumerate(sorted(all_runs, key=lambda x: x.started_at or 0), 1):
        if r.run_id == run_id:
            version = i
            break

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
        strategy_name=strategy_name,
        version=version,
    )


@router.delete("/runs/{run_id}", status_code=204)
async def delete_run(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> None:
    """Delete a backtest run and all associated data."""
    deleted = state.backtest_store.delete_run(run_id)
    if not deleted:
        raise HTTPException(404, f"Run not found: {run_id}")


@router.post("/runs/{run_id}/action")
async def run_action(
    run_id: str,
    action: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Control a backtest run. Cancel is cooperative and checked between heavy phases."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")
    if action != "cancel":
        raise HTTPException(400, f"Unsupported backtest action: {action}")
    if run.status not in {"queued", "running"}:
        return {
            "run_id": run_id,
            "action": action,
            "status": run.status,
            "accepted": False,
        }
    state.backtest_cancel_requests.add(run_id)
    ws_manager.update_progress(
        run_id, "backtest", "cancelling", 0.0, "Cancel requested"
    )
    await ws_manager.broadcast_progress(run_id)
    return {
        "run_id": run_id,
        "action": action,
        "status": "cancelling",
        "accepted": True,
    }


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
            entry_id=getattr(t, "entry_id", None),
            exit_id=getattr(t, "exit_id", None),
            entry_time=t.entry_time,
            exit_time=t.exit_time,
            direction=t.direction,
            entry_price=t.entry_price,
            exit_price=t.exit_price,
            stop_price=getattr(t, "stop_price", None),
            take_profit_price=getattr(t, "take_profit_price", None),
            qty=t.qty,
            net_profit=t.net_pnl,
            gross_profit=getattr(t, "gross_pnl", None),
            bars_held=getattr(t, "bars_held", None),
            exit_reason=getattr(t, "exit_reason", None),
        )
        for t in trades
    ]


@router.get("/progress/{run_id}", response_model=BacktestProgress | None)
async def get_progress(run_id: str) -> BacktestProgress | None:
    """Get current progress of a backtest run."""
    p = ws_manager.get_progress(run_id)
    if p is None:
        return None

    detail = p.get("detail") or {}
    if (
        isinstance(detail, dict)
        and "bars_processed" in detail
        and "total_bars" in detail
    ):
        return BacktestProgress(
            run_id=p["operation_id"],
            status=p["status"],
            bars_processed=int(detail.get("bars_processed") or 0),
            total_bars=int(detail.get("total_bars") or 0),
            pct=p["pct"],
            message=p.get("message", ""),
        )

    # Parse bars info from messages like "Bars: 5000/100000"
    import re

    message = p.get("message", "")
    bars_processed = 0
    total_bars = 0
    m = re.search(r"Bars:\s*([\d,]+)\s*/\s*([\d,]+)", message)
    if m:
        bars_processed = int(m.group(1).replace(",", ""))
        total_bars = int(m.group(2).replace(",", ""))

    return BacktestProgress(
        run_id=p["operation_id"],
        status=p["status"],
        bars_processed=bars_processed,
        total_bars=total_bars,
        pct=p["pct"],
        message=message,
    )


@router.get("/progress/{run_id}/detail")
async def get_progress_detail(run_id: str) -> dict[str, object] | None:
    """Get detailed progress including error message."""
    return ws_manager.get_progress(run_id)


# ── Backtest output routes ────────────────────────────────────────────────────


def _read_parquet_as_csv(path: str) -> str:
    """Read a parquet file and return as CSV string."""
    import pandas as pd

    df = pd.read_parquet(path)
    return df.to_csv(index=False)


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
    except ValueError:
        return False
    return True


def _backtest_artifact_root(state: GatewayState) -> Path | None:
    data_dir = getattr(state.backtest_store, "_data_dir", None)
    if data_dir is None:
        return None
    return Path(data_dir).expanduser().resolve()


def _safe_backtest_artifact_path(state: GatewayState, raw_path: str) -> Path | None:
    path = Path(raw_path).expanduser()
    root = _backtest_artifact_root(state)
    if root is None:
        return path
    resolved = path.resolve(strict=False)
    if not _path_is_under(resolved, root):
        log.warning(
            "unsafe_backtest_artifact_path",
            path=str(path),
            allowed_root=str(root),
        )
        return None
    return path


def _export_artifact_summary(state: GatewayState, artifact) -> dict[str, object]:
    summary: dict[str, object] = {"type": artifact.artifact_type}
    path = _safe_backtest_artifact_path(state, str(artifact.path))
    if path is not None:
        summary["filename"] = path.name
    return summary


def _get_artifact_path(
    state: GatewayState, run_id: str, artifact_type: str
) -> str | None:
    """Find artifact path by type for a run."""
    artifacts = state.backtest_store.list_artifacts(run_id)
    for a in artifacts:
        if a.artifact_type == artifact_type:
            path = _safe_backtest_artifact_path(state, str(a.path))
            return str(path) if path is not None else None
    return None


@router.get("/runs/{run_id}/equity")
async def get_run_equity(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get equity curve data for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    path = _get_artifact_path(state, run_id, "equity_curve")
    if path is None:
        raise HTTPException(404, "Equity curve not available for this run")

    try:
        csv_data = _read_parquet_as_csv(path)
        return {"run_id": run_id, "format": "csv", "data": csv_data}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read equity curve: {exc}")


@router.get("/runs/{run_id}/plots")
async def get_run_plots(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get plot outputs data for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    path = _get_artifact_path(state, run_id, "plot_outputs")
    if path is None:
        raise HTTPException(404, "Plot outputs not available for this run")

    try:
        csv_data = _read_parquet_as_csv(path)
        return {"run_id": run_id, "format": "csv", "data": csv_data}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read plot outputs: {exc}")


@router.get("/runs/{run_id}/bar-outputs")
async def get_run_bar_outputs(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get bar-level outputs for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    path = _get_artifact_path(state, run_id, "bar_outputs")
    if path is None:
        raise HTTPException(404, "Bar outputs not available for this run")

    try:
        csv_data = _read_parquet_as_csv(path)
        return {"run_id": run_id, "format": "csv", "data": csv_data}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read bar outputs: {exc}")


@router.get("/runs/{run_id}/report")
async def get_run_report(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Get the markdown report for a backtest run."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    path = _get_artifact_path(state, run_id, "report_md")
    if path is None:
        raise HTTPException(404, "Report not available for this run")

    from pathlib import Path

    try:
        content = Path(path).read_text(encoding="utf-8")
        return {"run_id": run_id, "format": "markdown", "data": content}
    except Exception as exc:
        raise HTTPException(500, f"Failed to read report: {exc}")


@router.get("/runs/{run_id}/export")
async def export_run(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, object]:
    """Export all backtest artifacts as a summary (equity + trades + metrics + report)."""
    run = state.backtest_store.get_run(run_id)
    if run is None:
        raise HTTPException(404, f"Run not found: {run_id}")

    result: dict[str, object] = {"run_id": run_id, "strategy_id": run.strategy_id}

    # Metrics
    metrics = state.backtest_store.get_metrics(run_id)
    if metrics:
        result["metrics"] = metrics

    # Trades
    trades = state.backtest_store.list_trades(run_id)
    result["trades"] = [
        {
            "trade_id": t.trade_id,
            "entry_time": t.entry_time,
            "exit_time": t.exit_time,
            "direction": t.direction,
            "entry_price": t.entry_price,
            "exit_price": t.exit_price,
            "stop_price": getattr(t, "stop_price", None),
            "take_profit_price": getattr(t, "take_profit_price", None),
            "qty": t.qty,
            "net_profit": t.net_pnl,
        }
        for t in trades
    ]

    # Artifacts list
    artifacts = state.backtest_store.list_artifacts(run_id)
    result["artifacts"] = [_export_artifact_summary(state, a) for a in artifacts]

    return result
