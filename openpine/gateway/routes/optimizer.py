"""Optimizer routes — validation and real isolated search."""

from __future__ import annotations

import asyncio
import json
import math
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException

from openpine._compat import structlog
from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.schemas import (
    OptimizerDryRunRequest,
    OptimizerDryRunResponse,
    OptimizerChampion,
    OptimizerSearchRequest,
    OptimizerSearchResponse,
    OptimizerTrialSummary,
)

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/optimizer", tags=["optimizer"])


def _optimizer_strategy(state: GatewayState, strategy_id: str):
    registry = getattr(state, "strategy_registry", None)
    getter = getattr(registry, "get_strategy", None)
    if getter is None:
        raise HTTPException(503, "Strategy registry is unavailable")
    try:
        return getter(strategy_id)
    except KeyError as exc:
        raise HTTPException(404, f"Strategy not found: {strategy_id}") from exc


def _optimizer_backtest_config(
    state: GatewayState,
    strategy,
    *,
    from_ms: int,
    to_ms: int,
    semantic_profile: str,
):
    from openpine.exchange_metadata import (
        default_price_tick,
        default_qty_rounding_mode,
        default_qty_step,
    )
    from openpine.runtime.declaration_args import artifact_strategy_declaration_args
    from openpine.runtime.engine import BacktestRunConfig

    artifact = state.artifact_store.get_artifact(
        strategy.artifact_id, strategy.pine_id
    )
    decl_args = artifact_strategy_declaration_args(artifact)
    commission_type = {
        "cash_per_order": "fixed_per_order",
        "cash_per_contract": "fixed_per_contract",
    }.get(
        str(decl_args.get("commission_type", "none")),
        decl_args.get("commission_type", "none"),
    )
    return BacktestRunConfig(
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        start_time=from_ms,
        end_time=to_ms,
        exchange=strategy.exchange,
        market_type=strategy.market_type,
        initial_capital=decl_args.get("initial_capital", 10_000.0),
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
        process_orders_on_close=bool(decl_args.get("process_orders_on_close", False)),
        calc_on_order_fills=bool(decl_args.get("calc_on_order_fills", False)),
        calc_on_every_tick=bool(decl_args.get("calc_on_every_tick", False)),
        use_bar_magnifier=bool(decl_args.get("use_bar_magnifier", False)),
        qty_step=default_qty_step(
            strategy.exchange, strategy.market_type, strategy.symbol
        ),
        qty_rounding_mode=default_qty_rounding_mode(
            strategy.exchange, strategy.market_type, strategy.symbol
        ),
        mintick=default_price_tick(
            strategy.exchange, strategy.market_type, strategy.symbol
        )
        or 0.01,
        export_resume_state=False,
        content_hash_enabled=True,
        collect_events=True,
        collect_order_lifecycle=True,
        capture_plots=False,
        semantic_profile=semantic_profile,
    )


def _numeric_metrics(metrics: object) -> dict[str, float]:
    if not isinstance(metrics, dict):
        return {}
    out: dict[str, float] = {}
    for name, value in metrics.items():
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        number = float(value)
        if math.isfinite(number):
            out[str(name)] = number
    return out


def _trial_summaries(result) -> list[OptimizerTrialSummary]:
    summaries: list[OptimizerTrialSummary] = []
    for item in getattr(result, "trial_metadata", ()) or ():
        objective = item.get("objective_value")
        if not isinstance(objective, (int, float)) or isinstance(objective, bool):
            objective = None
        elif not math.isfinite(float(objective)):
            objective = None
        summaries.append(
            OptimizerTrialSummary(
                id=item.get("id"),
                status=item.get("status"),
                objective_value=objective,
                params_hash=item.get("params_hash"),
                result_content_hash=item.get("result_content_hash"),
            )
        )
    return summaries


def _terminalize_optimizer_job(state: GatewayState, result) -> None:
    from openpine.jobs.persist import JobV1Error

    store = getattr(state, "job_store", None)
    if store is None:
        return
    try:
        if result.status == "completed":
            store.mark_succeeded(
                result.optimization_id,
                result_artifact_refs=[f"optimizer:{result.optimization_id}"],
            )
        else:
            store.mark_failed(
                result.optimization_id,
                error_code="OPTIMIZER_SEARCH_FAILED",
            )
    except (JobV1Error, KeyError) as exc:
        log.warning(
            "optimizer_job_terminalize_failed",
            job_id=result.optimization_id,
            error_type=exc.__class__.__name__,
        )


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


@router.post("/search", response_model=OptimizerSearchResponse)
async def optimizer_search(
    req: OptimizerSearchRequest,
    state: GatewayState = Depends(get_state),
) -> OptimizerSearchResponse:
    """Run external optimizer search over isolated generated artifact bytes."""
    from openpine.gateway.side_effects import persist_gateway_job, require_http_admit

    require_http_admit("optimize")
    from openpine.admission import admit_semantic_profile
    from openpine_contracts import AdmitError

    strategy = _optimizer_strategy(state, req.strategy_id)
    try:
        admitted = admit_semantic_profile(
            profile=req.semantic_profile or getattr(strategy, "semantic_profile", None),
            source="backtest",
            allow_legacy=req.allow_legacy,
        )
    except AdmitError as exc:
        raise HTTPException(403, exc.message) from exc

    try:
        from openpine.gateway.routes.backtest import (
            _confirmed_htf_bars_for_backtest,
            _market_data_query_for_strategy,
            _parse_date_ms,
        )
        from openpine.optimizer import OptimizerRunConfig, OptimizerService
        from openpine.optimizer.isolated_runner import IsolatedOptimizerRunner
        from openpine.runtime.isolated_run import capture_generated_source

        from_ms = _parse_date_ms(req.from_time)
        to_ms = _parse_date_ms(req.to_time)
        if to_ms <= from_ms:
            raise HTTPException(400, "to_time must be greater than from_time")
        query = _market_data_query_for_strategy(strategy, from_ms, to_ms)
        series = await asyncio.to_thread(state.orchestrator.load_bars, query)
        bars = list(getattr(series, "bars", series))
        if not bars:
            raise HTTPException(400, "Optimizer search requires non-empty market data")
        source = await asyncio.to_thread(
            capture_generated_source, strategy.pine_id, strategy.artifact_id
        )
        stamped_htf = await asyncio.to_thread(
            _confirmed_htf_bars_for_backtest,
            bars,
            strategy=strategy,
            requested_timeframe=req.htf_timeframe,
            load_bars=state.orchestrator.load_bars,
            from_ms=from_ms,
            to_ms=to_ms,
        )
        engine_config = _optimizer_backtest_config(
            state,
            strategy,
            from_ms=from_ms,
            to_ms=to_ms,
            semantic_profile=admitted.value,
        )
        base_params = json.loads(strategy.params_json or "{}")
        if not isinstance(base_params, dict):
            raise HTTPException(400, "Strategy params_json must contain an object")
        runner = IsolatedOptimizerRunner(
            source=source,
            bars=bars,
            config=engine_config,
            base_params=base_params,
            htf_bars=stamped_htf,
        )
        state_config = getattr(state, "config", None)
        output_root = Path(getattr(state_config, "data_dir", ".")) / "optimizer"
        run_config = OptimizerRunConfig(
            strategy_id=strategy.strategy_id,
            trials=req.trials,
            artifact_id=strategy.artifact_id,
            params_hash=strategy.params_hash,
            data_query={
                "exchange": strategy.exchange,
                "market_type": strategy.market_type,
                "symbol": strategy.symbol,
                "timeframe": strategy.timeframe,
                "from_time": from_ms,
                "to_time": to_ms,
                "htf_timeframe": req.htf_timeframe,
            },
            parameters=tuple(parameter.model_dump() for parameter in req.parameters),
            runner=runner,
            objective=req.objective,
            output_dir=output_root,
            storage_backend="json",
        )
        service = OptimizerService()
        ref = await asyncio.to_thread(service.adapter.start_optimization, run_config)
        result = await asyncio.to_thread(service.adapter.get_result, ref.optimization_id)
        persist_gateway_job(
            state,
            job_id=result.optimization_id,
            kind="optimize",
            actor="gateway",
            idempotency_key=result.optimization_id,
            input_artifact_refs=[f"artifact:{strategy.artifact_id}"],
            semantic_profile=admitted.value,
        )
        _terminalize_optimizer_job(state, result)
        champion = None
        if result.status == "completed" and result.best_params:
            champion = OptimizerChampion(
                params=dict(result.best_params),
                metrics=_numeric_metrics(result.metrics),
            )
        return OptimizerSearchResponse(
            optimization_id=result.optimization_id,
            strategy_id=result.strategy_id,
            objective=req.objective,
            status=result.status,
            trials_requested=result.trials_requested,
            trials_completed=result.trials_completed,
            champion=champion,
            trial_status_counts=dict(result.trial_status_counts),
            trials=_trial_summaries(result),
            uses_backtest_engine_path=bool(result.uses_backtest_engine_path),
        )
    except HTTPException:
        raise
    except (TypeError, ValueError) as exc:
        raise HTTPException(400, str(exc)) from exc
    except Exception as exc:
        log.error("optimizer_search_failed", error_type=exc.__class__.__name__)
        raise HTTPException(500, "Optimizer search failed") from exc
