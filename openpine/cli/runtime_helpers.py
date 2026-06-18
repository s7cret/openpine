"""Runtime helper functions for OpenPine CLI commands.

This module keeps the Click entrypoint small while preserving the legacy
``openpine.cli.main`` private helper surface through re-exports.
"""

from __future__ import annotations

import hashlib
import inspect
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace


from openpine.exchange_metadata import (
    default_qty_rounding_mode as metadata_default_qty_rounding_mode,
)
from openpine.exchange_metadata import default_qty_step
from openpine.timezones import parse_timestamp_ms


def _fmt_utc_ms(timestamp_ms: int) -> str:
    """Format a millisecond timestamp without deprecated utcfromtimestamp()."""
    return (
        f"{datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc):%Y-%m-%d %H:%M:%S}"
    )


def _fmt_utc_seconds(timestamp_seconds: int) -> str:
    return (
        f"{datetime.fromtimestamp(timestamp_seconds, timezone.utc):%Y-%m-%d %H:%M:%S}"
    )


def _fmt_utc_ms_as(timestamp_ms: int, fmt: str) -> str:
    return datetime.fromtimestamp(timestamp_ms / 1000, timezone.utc).strftime(fmt)


def _default_qty_step(exchange: str, market_type: str, symbol: str) -> float | None:
    return default_qty_step(exchange, market_type, symbol)


def _default_qty_rounding_mode(exchange: str, market_type: str, symbol: str) -> str:
    return metadata_default_qty_rounding_mode(exchange, market_type, symbol)


def _parse_cli_date_ms(value: str | None, default: int) -> int:
    return parse_timestamp_ms(value, default)


def _plot_record_count(plots) -> int:
    if plots is None:
        return 0
    if isinstance(plots, list):
        return len(plots)
    if hasattr(plots, "get_records"):
        return len(plots.get_records())
    return 0


def _bars_data_fingerprint(bars) -> str:
    payload = [
        (
            int(bar.time),
            int(getattr(bar, "time_close", 0)),
            float(bar.open),
            float(bar.high),
            float(bar.low),
            float(bar.close),
            None if getattr(bar, "volume", None) is None else float(bar.volume),
        )
        for bar in bars
    ]
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def _build_strategy_backtest_config(
    *,
    strategy,
    decl_args: dict,
    start_ms: int,
    end_ms: int,
    requested_start_ms: int | None = None,
    warmup_bars: int = 0,
    effective_pre_bars: int = 0,
    capture_plots: bool,
    capture_from_ms: int | None,
    capture_to_ms: int | None,
    config_cls,
):
    from openpine.runtime.declaration_args import normalize_strategy_declaration_args

    decl_args = normalize_strategy_declaration_args(decl_args)
    visible_start_ms = (
        requested_start_ms if requested_start_ms is not None else start_ms
    )
    commission_type = {
        "cash_per_order": "fixed_per_order",
        "cash_per_contract": "fixed_per_contract",
    }.get(
        str(decl_args.get("commission_type", "none")),
        decl_args.get("commission_type", "none"),
    )
    kwargs = {
        "symbol": strategy.symbol,
        "timeframe": strategy.timeframe,
        "start_time": visible_start_ms,
        "end_time": end_ms,
        "exchange": strategy.exchange.lower(),
        "market_type": strategy.market_type.lower(),
        "initial_capital": decl_args.get("initial_capital", 10000.0),
        "default_qty_type": decl_args.get("default_qty_type", "fixed"),
        "default_qty_value": decl_args.get("default_qty_value", 1.0),
        "commission_type": commission_type,
        "commission_value": decl_args.get("commission_value", 0.0),
        "slippage": decl_args.get("slippage", 0.0),
        "slippage_type": decl_args.get("slippage_type", "tick"),
        "exit_matching": decl_args.get("close_entries_rule", "fifo").upper(),
        "pyramiding": decl_args.get("pyramiding", 0),
        "margin_long": decl_args.get("margin_long", 100.0),
        "margin_short": decl_args.get("margin_short", 100.0),
        "process_orders_on_close": bool(
            decl_args.get("process_orders_on_close", False)
        ),
        "calc_on_order_fills": bool(decl_args.get("calc_on_order_fills", False)),
        "calc_on_every_tick": bool(decl_args.get("calc_on_every_tick", False)),
        "use_bar_magnifier": bool(decl_args.get("use_bar_magnifier", False)),
        "qty_step": _default_qty_step(
            strategy.exchange, strategy.market_type, strategy.symbol
        ),
        "qty_rounding_mode": _default_qty_rounding_mode(
            strategy.exchange,
            strategy.market_type,
            strategy.symbol,
        ),
        "max_bars_back": warmup_bars,
        "score_start_time": visible_start_ms if effective_pre_bars > 0 else None,
        "score_end_time": end_ms if effective_pre_bars > 0 else None,
        "max_pre_bars": warmup_bars,
        "warmup_metadata": (
            {"recommended_pre_bars_raw": warmup_bars} if warmup_bars > 0 else None
        ),
        "export_resume_state": True,
        "content_hash_enabled": False,
        "collect_events": False,
        "collect_order_lifecycle": False,
        "capture_plots": capture_plots,
        "plot_from_ms": capture_from_ms if capture_plots else None,
        "plot_to_ms": capture_to_ms if capture_plots else None,
    }
    supported = set(inspect.signature(config_cls).parameters)
    return config_cls(
        **{key: value for key, value in kwargs.items() if key in supported}
    )


def _build_strategy_replay_config(
    *,
    strategy,
    decl_args: dict,
    start_ms: int,
    end_ms: int,
    config_cls,
):
    from openpine.runtime.declaration_args import normalize_strategy_declaration_args

    decl_args = normalize_strategy_declaration_args(decl_args)
    return config_cls(
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        start_time=start_ms,
        end_time=end_ms,
        initial_capital=decl_args.get("initial_capital", 10000.0),
        default_qty_type=decl_args.get("default_qty_type", "fixed"),
        default_qty_value=decl_args.get("default_qty_value", 1.0),
        commission_type=decl_args.get("commission_type", "none"),
        commission_value=decl_args.get("commission_value", 0.0),
        exit_matching=decl_args.get("close_entries_rule", "fifo").upper(),
        pyramiding=decl_args.get("pyramiding", 0),
        qty_step=_default_qty_step(
            strategy.exchange, strategy.market_type, strategy.symbol
        ),
        qty_rounding_mode=_default_qty_rounding_mode(
            strategy.exchange, strategy.market_type, strategy.symbol
        ),
    )


def _get_strategy_or_exit(*, registry, strategy_id: str, console):
    try:
        return registry.get_strategy(strategy_id)
    except KeyError:
        console.print(f"[red]Strategy not found: {strategy_id}[/red]")
        sys.exit(1)


def _print_strategy_command_header(
    *,
    label: str,
    strategy_id: str,
    strategy,
    from_date: str | None,
    to_date: str | None,
    console,
) -> None:
    console.print(f"[bold]{label}: {strategy_id}[/bold]")
    console.print(f"  strategy:   {strategy.name}")
    console.print(f"  artifact:   {strategy.artifact_id}")
    console.print(f"  params:     {strategy.params_json}")
    console.print(f"  symbol:     {strategy.symbol}")
    console.print(f"  exchange:   {strategy.exchange}")
    console.print(f"  market:     {strategy.market_type}")
    console.print(f"  timeframe:  {strategy.timeframe}")
    console.print(f"  from:       {from_date or 'N/A'}")
    console.print(f"  to:         {to_date or 'N/A'}")


def _strategy_backtest_readiness_error(strategy) -> str | None:
    if not strategy.pine_id:
        return (
            "Strategy has no pine_id. Recreate it with: "
            "openpine strategy create <name> --pine <pine-name> ..."
        )
    if not strategy.artifact_id:
        return (
            "Strategy has no compiled artifact. Compile first with: "
            "openpine pine compile <pine-name>"
        )
    return None


def _exit_if_strategy_not_ready_for_backtest(
    *, strategy, strategy_id: str, registry, console
) -> None:
    readiness_error = _strategy_backtest_readiness_error(strategy)
    if not readiness_error:
        return
    console.print(f"[red]{readiness_error}[/red]")
    registry.update_status(strategy_id, "paused")
    sys.exit(1)


def _parse_strategy_backtest_window(
    *,
    from_date: str | None,
    to_date: str | None,
    capture_from: str | None,
    capture_to: str | None,
    now_ms: int,
) -> tuple[int, int, int | None, int | None]:
    end_ms = _parse_cli_date_ms(to_date, now_ms)
    start_ms = _parse_cli_date_ms(from_date, 0)
    capture_from_ms = (
        _parse_cli_date_ms(capture_from, start_ms) if capture_from else None
    )
    capture_to_ms = _parse_cli_date_ms(capture_to, end_ms) if capture_to else None
    return start_ms, end_ms, capture_from_ms, capture_to_ms


def _parse_valid_strategy_backtest_window(
    *,
    from_date: str | None,
    to_date: str | None,
    capture_from: str | None,
    capture_to: str | None,
    now_ms: int,
    registry,
    strategy_id: str,
    console,
) -> tuple[int, int, int | None, int | None]:
    start_ms, end_ms, capture_from_ms, capture_to_ms = _parse_strategy_backtest_window(
        from_date=from_date,
        to_date=to_date,
        capture_from=capture_from,
        capture_to=capture_to,
        now_ms=now_ms,
    )
    if start_ms >= end_ms:
        console.print("[red]Invalid backtest window: --from must be before --to[/red]")
        registry.update_status(strategy_id, "paused")
        sys.exit(1)
    return start_ms, end_ms, capture_from_ms, capture_to_ms


def _print_backtest_result_summary(result, *, console) -> None:
    console.print("[green]Backtest completed[/green]")
    console.print(f"  status:     {result.status}")
    console.print(f"  bars:       {result.bars_processed}")
    console.print(
        f"  engine:     {'backtest_engine' if result.uses_backtest_engine else 'unknown'}"
    )


def _load_strategy_backtest_class(*, strategy, load_strategy_class, perf_counter):
    t0 = perf_counter()
    strategy_class = load_strategy_class(
        strategy.pine_id,
        strategy.artifact_id,
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
    )
    return strategy_class, perf_counter() - t0


def _load_strategy_backtest_class_or_exit(
    *,
    strategy,
    strategy_id: str,
    registry,
    load_strategy_class,
    artifact_error_cls,
    perf_counter,
    console,
) -> tuple[object, float]:
    try:
        return _load_strategy_backtest_class(
            strategy=strategy,
            load_strategy_class=load_strategy_class,
            perf_counter=perf_counter,
        )
    except artifact_error_cls as exc:
        console.print(f"[red]{exc}[/red]")
        registry.update_status(strategy_id, "paused")
        sys.exit(1)


def _exit_if_no_strategy_bars(
    *,
    bars,
    strategy,
    start_ms: int,
    end_ms: int,
    from_date: str | None,
    to_date: str | None,
    registry,
    strategy_id: str,
    console,
) -> None:
    if bars:
        return
    console.print(
        f"[red]No candle data found for {strategy.symbol} {strategy.timeframe} "
        f"in {start_ms}-{end_ms}.[/red]"
    )
    console.print(
        f"[yellow]Run: openpine data backfill {strategy.symbol} {strategy.timeframe} "
        f"--from {from_date or start_ms} --to {to_date or end_ms}[/yellow]"
    )
    registry.update_status(strategy_id, "paused")
    sys.exit(1)


def _build_strategy_backtest_params_and_config(
    *,
    strategy,
    decl_args: dict,
    params_json: str | None,
    start_ms: int,
    end_ms: int,
    requested_start_ms: int | None,
    warmup_bars: int,
    effective_pre_bars: int,
    capture_plots: bool,
    capture_from_ms: int | None,
    capture_to_ms: int | None,
    config_cls,
) -> tuple[dict, object]:
    import json as _json

    params = _json.loads(params_json) if params_json else {}
    config = _build_strategy_backtest_config(
        strategy=strategy,
        decl_args=decl_args,
        start_ms=start_ms,
        end_ms=end_ms,
        requested_start_ms=requested_start_ms,
        warmup_bars=warmup_bars,
        effective_pre_bars=effective_pre_bars,
        capture_plots=capture_plots,
        capture_from_ms=capture_from_ms,
        capture_to_ms=capture_to_ms,
        config_cls=config_cls,
    )
    return params, config


def _prepare_strategy_backtest_inputs(
    *,
    strategy,
    strategy_id: str,
    from_date: str | None,
    to_date: str | None,
    capture_plots: bool,
    capture_from: str | None,
    capture_to: str | None,
    history_from: str | None,
    warmup_bars: int,
    gap_policy: str,
    now_ms: int,
    registry,
    deps,
    perf_counter,
    console,
):
    start_ms, end_ms, capture_from_ms, capture_to_ms = (
        _parse_valid_strategy_backtest_window(
            from_date=from_date,
            to_date=to_date,
            capture_from=capture_from,
            capture_to=capture_to,
            now_ms=now_ms,
            registry=registry,
            strategy_id=strategy_id,
            console=console,
        )
    )
    requested_start_ms = start_ms
    if history_from:
        history_start_ms = _parse_cli_date_ms(history_from, start_ms)
        if history_start_ms >= start_ms:
            console.print(
                "[red]Invalid history window: --history-from must be before --from[/red]"
            )
            registry.update_status(strategy_id, "paused")
            sys.exit(1)
        start_ms = history_start_ms
    elif warmup_bars > 0:
        timeframe = deps.parse_timeframe(strategy.timeframe)
        if timeframe.duration_ms is None:
            console.print(
                "[red]--warmup-bars requires a fixed-duration timeframe[/red]"
            )
            registry.update_status(strategy_id, "paused")
            sys.exit(1)
        start_ms = max(0, start_ms - warmup_bars * timeframe.duration_ms)

    timings: dict[str, float] = {}
    strategy_class, timings["load_artifact_sec"] = (
        _load_strategy_backtest_class_or_exit(
            strategy=strategy,
            strategy_id=strategy_id,
            registry=registry,
            load_strategy_class=deps.load_strategy_class_from_artifact,
            artifact_error_cls=deps.BacktestArtifactError,
            perf_counter=perf_counter,
            console=console,
        )
    )

    bars, provider, data_fetch_info, timings["data_load_sec"] = (
        _load_strategy_backtest_bars(
            strategy=strategy,
            start_ms=start_ms,
            end_ms=end_ms,
            bar_query_cls=deps.BarQuery,
            instrument_key_cls=deps.InstrumentKey,
            parse_timeframe_func=deps.parse_timeframe,
            orchestrator_cls=deps.DataOrchestrator,
            provider_factory=deps.create_local_marketdata_provider_adapter,
            gap_policy=gap_policy,
            console=console,
        )
    )
    _exit_if_no_strategy_bars(
        bars=bars,
        strategy=strategy,
        start_ms=start_ms,
        end_ms=end_ms,
        from_date=from_date,
        to_date=to_date,
        registry=registry,
        strategy_id=strategy_id,
        console=console,
    )

    decl_args = _strategy_backtest_declaration_args(
        artifact_store_cls=deps.ArtifactStore,
        strategy=strategy,
    )
    effective_pre_bars = sum(1 for bar in bars if int(bar.time) < requested_start_ms)
    params, config = _build_strategy_backtest_params_and_config(
        strategy=strategy,
        decl_args=decl_args,
        params_json=strategy.params_json,
        start_ms=start_ms,
        end_ms=end_ms,
        requested_start_ms=requested_start_ms,
        warmup_bars=warmup_bars or effective_pre_bars,
        effective_pre_bars=effective_pre_bars,
        capture_plots=capture_plots,
        capture_from_ms=capture_from_ms,
        capture_to_ms=capture_to_ms,
        config_cls=deps.BacktestRunConfig,
    )
    return SimpleNamespace(
        start_ms=start_ms,
        end_ms=end_ms,
        strategy_class=strategy_class,
        bars=bars,
        provider=provider,
        data_fetch_info=data_fetch_info,
        params=params,
        config=config,
        requested_start_ms=requested_start_ms,
        effective_pre_bars=effective_pre_bars,
        timings=timings,
    )


def _prepare_strategy_replay_inputs(
    *,
    strategy,
    strategy_id: str,
    from_date: str | None,
    to_date: str | None,
    now_ms: int,
    registry,
    load_strategy_class,
    artifact_error_cls,
    artifact_store_cls,
    bar_query_cls,
    instrument_key_cls,
    parse_timeframe_func,
    orchestrator_cls,
    config_cls,
    perf_counter,
    console,
):
    start_ms, end_ms, _, _ = _parse_strategy_backtest_window(
        from_date=from_date,
        to_date=to_date,
        capture_from=None,
        capture_to=None,
        now_ms=now_ms,
    )
    if start_ms >= end_ms:
        console.print("[red]Invalid replay window: --from must be before --to[/red]")
        registry.update_status(strategy_id, "paused")
        sys.exit(1)

    try:
        strategy_class, _ = _load_strategy_backtest_class(
            strategy=strategy,
            load_strategy_class=load_strategy_class,
            perf_counter=perf_counter,
        )
    except artifact_error_cls as exc:
        console.print(f"[red]{exc}[/red]")
        registry.update_status(strategy_id, "paused")
        sys.exit(1)

    bars, _, _, _ = _load_strategy_backtest_bars(
        strategy=strategy,
        start_ms=start_ms,
        end_ms=end_ms,
        bar_query_cls=bar_query_cls,
        instrument_key_cls=instrument_key_cls,
        parse_timeframe_func=parse_timeframe_func,
        orchestrator_cls=orchestrator_cls,
        provider_factory=lambda: None,
        console=console,
    )
    _exit_if_no_strategy_bars(
        bars=bars,
        strategy=strategy,
        start_ms=start_ms,
        end_ms=end_ms,
        from_date=from_date,
        to_date=to_date,
        registry=registry,
        strategy_id=strategy_id,
        console=console,
    )

    decl_args = _strategy_backtest_declaration_args(
        artifact_store_cls=artifact_store_cls,
        strategy=strategy,
    )
    import json as _json

    params = _json.loads(strategy.params_json) if strategy.params_json else {}
    config = _build_strategy_replay_config(
        strategy=strategy,
        decl_args=decl_args,
        start_ms=start_ms,
        end_ms=end_ms,
        config_cls=config_cls,
    )
    return SimpleNamespace(
        strategy_class=strategy_class,
        bars=bars,
        params=params,
        config=config,
    )


def _build_strategy_backtest_run_request(
    *,
    strategy,
    start_ms: int,
    end_ms: int,
    request_cls,
):
    return request_cls(
        strategy_id=strategy.strategy_id,
        pine_id=strategy.pine_id,
        artifact_id=strategy.artifact_id,
        params_hash=strategy.params_hash,
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        exchange=strategy.exchange,
        market_type=strategy.market_type,
        from_time=start_ms,
        to_time=end_ms,
    )


def _prepare_strategy_backtest_runtime(strategy_class, console):
    return strategy_class, None


def _build_progress_callback(
    *, bars_total: int, console, progress_every: int | None = None
):
    if progress_every == 0:
        return None
    progress_every = max(
        1, progress_every if progress_every is not None else bars_total // 20
    )
    state = {"last_progress": 0}

    def _progress(done: int, total: int) -> None:
        if done == total or done - state["last_progress"] >= progress_every:
            state["last_progress"] = done
            console.print(f"[dim]runtime: {done}/{total} bars[/dim]")

    return _progress


def _parse_indicator_plot_window(
    *,
    from_date: str,
    to_date: str | None,
    compare_from: str | None,
    compare_to: str | None,
    parse_time_ms_func,
    now_ms: int,
) -> tuple[int | None, int, int | None, int | None]:
    start_ms = parse_time_ms_func(from_date)
    end_ms = parse_time_ms_func(to_date) or now_ms
    compare_from_ms = parse_time_ms_func(compare_from)
    compare_to_ms = parse_time_ms_func(compare_to)
    return start_ms, end_ms, compare_from_ms, compare_to_ms


def _load_pine_source_or_exit(*, registry_cls, name: str, console):
    registry = registry_cls()
    try:
        try:
            return registry.get_source(name)
        except KeyError:
            console.print(f"[red]Pine source not found: {name}[/red]")
            sys.exit(1)
    finally:
        registry.close()


def _require_active_pine_artifact(source, *, name: str, console) -> None:
    if source.active_artifact_id:
        return
    console.print(
        f"[red]Pine source {name} has no active artifact. "
        f"Compile it first with: openpine pine pine-compile {name}[/red]"
    )
    sys.exit(1)


def _load_generated_class_timed(*, source, load_generated_class, perf_counter):
    t0 = perf_counter()
    generated_class = load_generated_class(source.id, source.active_artifact_id)
    return generated_class, perf_counter() - t0


def _print_indicator_plot_header(
    *,
    name: str,
    source,
    symbol: str,
    exchange: str,
    market_type: str,
    timeframe: str,
    from_date: str,
    to_date: str | None,
    console,
) -> None:
    console.print(f"[bold]Indicator plots: {name}[/bold]")
    console.print(f"  artifact:   {source.active_artifact_id}")
    console.print(f"  symbol:     {symbol}")
    console.print(f"  exchange:   {exchange}")
    console.print(f"  market:     {market_type}")
    console.print(f"  timeframe:  {timeframe}")
    console.print(f"  from:       {from_date}")
    console.print(f"  to:         {to_date or 'now'}")


def _ensure_output_dir(output_dir: str) -> Path:
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    return output_path


def _strategy_backtest_dependencies():
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
    from openpine.artifacts import ArtifactStore
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.data.provider_adapter import create_local_marketdata_provider_adapter
    from openpine.runtime.engine import (
        BacktestArtifactError,
        BacktestEngineAdapter,
        BacktestRunConfig,
        load_strategy_class_from_artifact,
    )
    from openpine.storage import BacktestResultStore, BacktestRunRequest

    return SimpleNamespace(
        ArtifactStore=ArtifactStore,
        BacktestArtifactError=BacktestArtifactError,
        BacktestEngineAdapter=BacktestEngineAdapter,
        BacktestResultStore=BacktestResultStore,
        BacktestRunConfig=BacktestRunConfig,
        BacktestRunRequest=BacktestRunRequest,
        BarQuery=BarQuery,
        DataOrchestrator=DataOrchestrator,
        InstrumentKey=InstrumentKey,
        create_local_marketdata_provider_adapter=create_local_marketdata_provider_adapter,
        load_strategy_class_from_artifact=load_strategy_class_from_artifact,
        parse_timeframe=parse_timeframe,
    )


def _indicator_plot_dependencies():
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.data.provider_adapter import create_local_marketdata_provider_adapter
    from openpine.export import export_plot_records, parse_time_ms, write_json
    from openpine.pine.registry import SQLitePineSourceRegistry
    from openpine.runtime.engine import (
        BacktestArtifactError,
        load_generated_class_from_artifact,
    )

    return SimpleNamespace(
        BacktestArtifactError=BacktestArtifactError,
        BarQuery=BarQuery,
        DataOrchestrator=DataOrchestrator,
        InstrumentKey=InstrumentKey,
        SQLitePineSourceRegistry=SQLitePineSourceRegistry,
        create_local_marketdata_provider_adapter=create_local_marketdata_provider_adapter,
        export_plot_records=export_plot_records,
        load_generated_class_from_artifact=load_generated_class_from_artifact,
        parse_time_ms=parse_time_ms,
        parse_timeframe=parse_timeframe,
        write_json=write_json,
    )


def _load_indicator_plot_bars(
    *,
    symbol: str,
    exchange: str,
    market_type: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    bar_query_cls,
    instrument_key_cls,
    parse_timeframe_func,
    orchestrator_cls,
    provider_factory,
    console,
):
    strategy = SimpleNamespace(
        symbol=symbol,
        exchange=exchange,
        market_type=market_type,
        timeframe=timeframe,
    )
    return _load_strategy_backtest_bars(
        strategy=strategy,
        start_ms=start_ms,
        end_ms=end_ms,
        bar_query_cls=bar_query_cls,
        instrument_key_cls=instrument_key_cls,
        parse_timeframe_func=parse_timeframe_func,
        orchestrator_cls=orchestrator_cls,
        provider_factory=provider_factory,
        console=console,
    )


def _execute_indicator_plot_runtime(
    *,
    generated_class,
    bars,
    config,
    symbol: str,
    timeframe: str,
    provider,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
    progress_callback,
):
    from openpine.integrations import import_library

    import_library("backtest_engine")
    from backtest_engine.execution_backends.pine_runtime import PineRuntimeBackend

    return PineRuntimeBackend().execute(
        generated_class,
        bars,
        config=config,
        execution_window=None,
        runtime_kwargs={
            "symbol": symbol,
            "timeframe": timeframe,
            "data_provider": getattr(provider, "_provider", None),
            "plot_from_ms": compare_from_ms,
            "plot_to_ms": compare_to_ms,
            "progress_callback": progress_callback,
            "tv_export_barstate": True,
            "normalize_time_close_exclusive": True,
        },
        params={},
        is_indicator=True,
    )


def _run_indicator_plot_runtime(
    *,
    generated_class,
    bars,
    symbol: str,
    timeframe: str,
    exchange: str,
    market_type: str,
    provider,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
    progress_every: int,
    console,
    perf_counter,
) -> tuple[object, float]:
    t0 = perf_counter()
    config = _build_indicator_plot_config(
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        market_type=market_type,
        provider=provider,
    )
    backend_result = _execute_indicator_plot_runtime(
        generated_class=generated_class,
        bars=bars,
        config=config,
        symbol=symbol,
        timeframe=timeframe,
        provider=provider,
        compare_from_ms=compare_from_ms,
        compare_to_ms=compare_to_ms,
        progress_callback=_build_progress_callback(
            bars_total=len(bars),
            console=console,
            progress_every=progress_every,
        ),
    )
    return backend_result, perf_counter() - t0


def _write_indicator_plot_outputs(
    *,
    backend_result,
    output_path: Path,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
    export_plot_records_func,
    perf_counter,
) -> tuple[Path, int, float]:
    t0 = perf_counter()
    plots_csv = output_path / "plots.csv"
    plots_rows = export_plot_records_func(
        list(getattr(backend_result, "plots", []) or []),
        plots_csv,
        from_ms=compare_from_ms,
        to_ms=compare_to_ms,
    )
    return plots_csv, plots_rows, perf_counter() - t0


def _write_indicator_plot_run_meta(
    *,
    name: str,
    source,
    symbol: str,
    exchange: str,
    market_type: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
    bars_total: int,
    data_fetch_info,
    plots_rows: int,
    timings: dict[str, float],
    plots_csv: Path,
    output_path: Path,
    write_json_func,
    console,
) -> None:
    meta = _build_indicator_plot_run_meta(
        name=name,
        source=source,
        symbol=symbol,
        exchange=exchange,
        market_type=market_type,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        compare_from_ms=compare_from_ms,
        compare_to_ms=compare_to_ms,
        bars_total=bars_total,
        data_fetch_info=data_fetch_info,
        plots_rows=plots_rows,
        timings=timings,
        plots_csv=plots_csv,
    )
    meta_path = output_path / "run_meta.json"
    write_json_func(meta_path, meta)
    console.print("[green]Indicator plots exported[/green]")
    console.print(f"  plots:     {plots_csv}")
    console.print(f"  rows:      {plots_rows}")
    console.print(f"  meta:      {meta_path}")


def _write_indicator_plot_run_outputs(
    *,
    deps,
    prepared,
    name: str,
    symbol: str,
    timeframe: str,
    exchange: str,
    market_type: str,
    output_dir: str,
    progress_every: int,
    timings: dict[str, float],
    start_total: float,
    perf_counter,
    console,
) -> None:
    output_path = _ensure_output_dir(output_dir)
    backend_result, timings["runtime_sec"] = _run_indicator_plot_runtime(
        generated_class=prepared.generated_class,
        bars=prepared.bars,
        symbol=symbol,
        timeframe=timeframe,
        exchange=exchange,
        market_type=market_type,
        provider=prepared.provider,
        compare_from_ms=prepared.compare_from_ms,
        compare_to_ms=prepared.compare_to_ms,
        progress_every=progress_every,
        console=console,
        perf_counter=perf_counter,
    )
    plots_csv, plots_rows, timings["export_sec"] = _write_indicator_plot_outputs(
        backend_result=backend_result,
        output_path=output_path,
        compare_from_ms=prepared.compare_from_ms,
        compare_to_ms=prepared.compare_to_ms,
        export_plot_records_func=deps.export_plot_records,
        perf_counter=perf_counter,
    )
    timings["total_sec"] = perf_counter() - start_total
    _write_indicator_plot_run_meta(
        name=name,
        source=prepared.source,
        symbol=symbol,
        exchange=exchange,
        market_type=market_type,
        timeframe=timeframe,
        start_ms=prepared.start_ms,
        end_ms=prepared.end_ms,
        compare_from_ms=prepared.compare_from_ms,
        compare_to_ms=prepared.compare_to_ms,
        bars_total=len(prepared.bars),
        data_fetch_info=prepared.data_fetch_info,
        plots_rows=plots_rows,
        timings=timings,
        plots_csv=plots_csv,
        output_path=output_path,
        write_json_func=deps.write_json,
        console=console,
    )


def _prepare_indicator_plot_inputs(
    *,
    name: str,
    symbol: str,
    timeframe: str,
    exchange: str,
    market_type: str,
    from_date: str,
    to_date: str | None,
    compare_from: str | None,
    compare_to: str | None,
    now_ms: int,
    registry_cls,
    parse_time_ms_func,
    load_generated_class,
    artifact_error_cls,
    bar_query_cls,
    instrument_key_cls,
    parse_timeframe_func,
    orchestrator_cls,
    provider_factory,
    perf_counter,
    console,
):
    source = _load_pine_source_or_exit(
        registry_cls=registry_cls,
        name=name,
        console=console,
    )
    _require_active_pine_artifact(source, name=name, console=console)

    start_ms, end_ms, compare_from_ms, compare_to_ms = _parse_indicator_plot_window(
        from_date=from_date,
        to_date=to_date,
        compare_from=compare_from,
        compare_to=compare_to,
        parse_time_ms_func=parse_time_ms_func,
        now_ms=now_ms,
    )
    if start_ms is None or start_ms >= end_ms:
        console.print("[red]Invalid run window: --from must be before --to[/red]")
        sys.exit(1)

    try:
        generated_class, load_artifact_sec = _load_generated_class_timed(
            source=source,
            load_generated_class=load_generated_class,
            perf_counter=perf_counter,
        )
    except artifact_error_cls as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    bars, provider, data_fetch_info, data_load_sec = _load_indicator_plot_bars(
        symbol=symbol,
        exchange=exchange,
        market_type=market_type,
        timeframe=timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        bar_query_cls=bar_query_cls,
        instrument_key_cls=instrument_key_cls,
        parse_timeframe_func=parse_timeframe_func,
        orchestrator_cls=orchestrator_cls,
        provider_factory=provider_factory,
        console=console,
    )
    if not bars:
        console.print(f"[red]No candle data found for {symbol} {timeframe}[/red]")
        sys.exit(1)

    return SimpleNamespace(
        source=source,
        start_ms=start_ms,
        end_ms=end_ms,
        compare_from_ms=compare_from_ms,
        compare_to_ms=compare_to_ms,
        generated_class=generated_class,
        bars=bars,
        provider=provider,
        data_fetch_info=data_fetch_info,
        timings={
            "load_artifact_sec": load_artifact_sec,
            "data_load_sec": data_load_sec,
        },
    )


def _build_strategy_backtest_run_meta(
    *,
    strategy,
    start_ms: int,
    end_ms: int,
    visible_start_ms: int | None = None,
    effective_pre_bars: int = 0,
    bars_total: int,
    data_fetch_info,
    result,
    capture_plots: bool,
    timings: dict[str, float],
):
    raw_result = result.raw_result
    plots = getattr(raw_result, "plots", None)
    return {
        "type": "strategy",
        "strategy_id": strategy.strategy_id,
        "strategy_name": strategy.name,
        "pine_id": strategy.pine_id,
        "artifact_id": strategy.artifact_id,
        "symbol": strategy.symbol,
        "timeframe": strategy.timeframe,
        "calculation_from": start_ms,
        "calculation_to": end_ms,
        "visible_from": visible_start_ms,
        "visible_to": end_ms,
        "effective_pre_bars": effective_pre_bars,
        "bars_total": bars_total,
        "data_fetch": data_fetch_info,
        "bars_processed": result.bars_processed,
        "trades_rows": len(getattr(raw_result, "trades", []) or []),
        "open_trades": len(getattr(raw_result, "open_trades", []) or []),
        "plots_records": _plot_record_count(plots) if capture_plots else 0,
        "process_next_bar_available": result.process_next_bar_available,
        "timings": timings,
    }


def _print_strategy_plot_capture_status(
    *, raw_result, capture_plots: bool, console
) -> None:
    if not capture_plots:
        return
    plots = getattr(raw_result, "plots", None)
    if plots:
        recs = (
            plots
            if isinstance(plots, list)
            else (plots.get_records() if hasattr(plots, "get_records") else [])
        )
        if recs:
            console.print(
                f"[green]  plots:      {len(recs)} plot records captured[/green]"
            )
        else:
            console.print("[yellow]  plots:      plot recorder empty[/yellow]")
    else:
        console.print(
            "[yellow]  plots:      plot outputs unavailable from engine result[/yellow]"
        )


def _load_strategy_backtest_bars(
    *,
    strategy,
    start_ms: int,
    end_ms: int,
    bar_query_cls,
    instrument_key_cls,
    parse_timeframe_func,
    orchestrator_cls,
    provider_factory,
    gap_policy: str = "fail",
    console,
):
    import time as _time

    query = _build_cli_bar_query(
        symbol=strategy.symbol,
        exchange=strategy.exchange,
        market_type=strategy.market_type,
        timeframe=strategy.timeframe,
        start_ms=start_ms,
        end_ms=end_ms,
        bar_query_cls=bar_query_cls,
        instrument_key_cls=instrument_key_cls,
        parse_timeframe_func=parse_timeframe_func,
        gap_policy=gap_policy,
    )
    orch = orchestrator_cls()
    provider = provider_factory()
    if provider:
        orch.set_provider(provider)
    console.print("[dim]data: loading bars[/dim]")
    t0 = _time.perf_counter()
    bars = orch.get_bars(query)
    data_load_sec = _time.perf_counter() - t0
    if bars:
        console.print(
            f"[green]data: {len(bars)} bars loaded in {data_load_sec:.2f}s[/green]"
        )
    data_fetch_info = getattr(
        getattr(provider, "_provider", None), "last_fetch_info", None
    )
    return bars, provider, data_fetch_info, data_load_sec


def _strategy_backtest_declaration_args(*, artifact_store_cls, strategy) -> dict:
    from openpine.runtime.declaration_args import artifact_strategy_declaration_args

    store = artifact_store_cls()
    artifact = store.get_artifact(strategy.artifact_id, strategy.pine_id)
    return artifact_strategy_declaration_args(artifact)


def _save_strategy_backtest_result(
    *,
    store,
    request_cls,
    strategy,
    start_ms: int,
    end_ms: int,
    visible_start_ms: int | None,
    effective_pre_bars: int,
    bars_total: int,
    data_fetch_info,
    result,
    capture_plots: bool,
    timings: dict[str, float],
    total_started: float,
    perf_counter,
    console,
):
    from openpine.config import OpenPineConfig
    from openpine.export import write_json

    t0 = perf_counter()
    run_request = _build_strategy_backtest_run_request(
        strategy=strategy,
        start_ms=start_ms,
        end_ms=end_ms,
        request_cls=request_cls,
    )
    run_id = store.create_run(run_request)
    raw_result = result.raw_result
    store.save_result(
        run_id=run_id,
        result=raw_result,
        trades=getattr(raw_result, "trades", []),
        equity_curve=getattr(raw_result, "equity_curve", None),
        plots=getattr(raw_result, "plots", None) if capture_plots else None,
    )
    timings["save_sec"] = perf_counter() - t0
    timings["total_sec"] = perf_counter() - total_started

    run_dir = (
        OpenPineConfig.load().data_dir / "backtests" / strategy.strategy_id / run_id
    )
    meta = _build_strategy_backtest_run_meta(
        strategy=strategy,
        start_ms=start_ms,
        end_ms=end_ms,
        visible_start_ms=visible_start_ms,
        effective_pre_bars=effective_pre_bars,
        bars_total=bars_total,
        data_fetch_info=data_fetch_info,
        result=result,
        capture_plots=capture_plots,
        timings=timings,
    )
    write_json(run_dir / "run_meta.json", meta)
    console.print(f"[green]Backtest saved:[/green] {run_id}")
    console.print(
        f"  trades:     {len(getattr(raw_result, 'trades', []))} closed + "
        f"{len(getattr(raw_result, 'open_trades', []))} open"
    )
    console.print(f"  artifacts:  {run_dir}/")
    _print_strategy_plot_capture_status(
        raw_result=raw_result,
        capture_plots=capture_plots,
        console=console,
    )
    return run_id, run_dir


def _save_strategy_backtest_result_safely(
    *,
    store,
    request_cls,
    strategy,
    start_ms: int,
    end_ms: int,
    visible_start_ms: int | None,
    effective_pre_bars: int,
    bars_total: int,
    data_fetch_info,
    result,
    capture_plots: bool,
    timings: dict[str, float],
    total_started: float,
    perf_counter,
    console,
) -> None:
    try:
        _save_strategy_backtest_result(
            store=store,
            request_cls=request_cls,
            strategy=strategy,
            start_ms=start_ms,
            end_ms=end_ms,
            visible_start_ms=visible_start_ms,
            effective_pre_bars=effective_pre_bars,
            bars_total=bars_total,
            data_fetch_info=data_fetch_info,
            result=result,
            capture_plots=capture_plots,
            timings=timings,
            total_started=total_started,
            perf_counter=perf_counter,
            console=console,
        )
    except Exception as exc:
        console.print(
            f"[yellow]Warning: failed to save backtest results: {exc}[/yellow]"
        )
        import traceback

        traceback.print_exc()


def _persist_strategy_backtest_result(
    *,
    deps,
    strategy,
    prepared,
    result,
    capture_plots: bool,
    timings: dict[str, float],
    total_started: float,
    perf_counter,
    console,
) -> None:
    bt_store = deps.BacktestResultStore()
    try:
        _save_strategy_backtest_result_safely(
            store=bt_store,
            request_cls=deps.BacktestRunRequest,
            strategy=strategy,
            start_ms=prepared.start_ms,
            end_ms=prepared.end_ms,
            visible_start_ms=prepared.requested_start_ms,
            effective_pre_bars=prepared.effective_pre_bars,
            bars_total=len(prepared.bars),
            data_fetch_info=prepared.data_fetch_info,
            result=result,
            capture_plots=capture_plots,
            timings=timings,
            total_started=total_started,
            perf_counter=perf_counter,
            console=console,
        )
    finally:
        bt_store.close()
    _save_strategy_resume_snapshot(
        strategy=strategy,
        prepared=prepared,
        result=result,
        console=console,
    )


def _save_strategy_resume_snapshot(*, strategy, prepared, result, console) -> None:
    resume_state = getattr(result, "resume_state", None)
    if resume_state is None:
        return
    try:
        from openpine.config import OpenPineConfig
        from openpine.state.store import StateStore

        store = StateStore(OpenPineConfig.load().data_dir / "state")
        meta = store.save_runtime_snapshot(
            strategy_id=strategy.strategy_id,
            artifact_id=strategy.artifact_id,
            params_hash=strategy.params_hash,
            instrument_key={
                "exchange": strategy.exchange.lower(),
                "market": strategy.market_type.lower(),
                "symbol": strategy.symbol.upper(),
            },
            timeframe={"canonical": strategy.timeframe},
            runtime_state=resume_state,
            bar_time=int(prepared.bars[-1].time) if prepared.bars else prepared.end_ms,
            reason="backtest_complete",
            data_fingerprint=_bars_data_fingerprint(prepared.bars),
        )
        if meta is not None:
            console.print(f"[green]State snapshot saved:[/green] {meta.snapshot_id}")
    except Exception as exc:
        console.print(f"[yellow]Warning: failed to save state snapshot: {exc}[/yellow]")


def _run_strategy_backtest_adapter(
    *,
    adapter_cls,
    strategy_class,
    bars,
    config,
    params: dict,
    provider,
    effective_pre_bars: int | None = None,
    console,
    perf_counter,
):
    selected_strategy_class, backend = _prepare_strategy_backtest_runtime(
        strategy_class,
        console,
    )
    t0 = perf_counter()
    result = adapter_cls().run(
        selected_strategy_class,
        bars,
        config,
        params=params,
        execution_backend=backend,
        progress_callback=_build_progress_callback(
            bars_total=len(bars), console=console
        ),
        runtime_data_provider=getattr(provider, "_provider", None),
        effective_pre_bars=effective_pre_bars,
    )
    return result, perf_counter() - t0


def _run_strategy_backtest_or_exit(
    *,
    deps,
    prepared,
    registry,
    strategy_id: str,
    console,
    perf_counter,
):
    try:
        return _run_strategy_backtest_adapter(
            adapter_cls=deps.BacktestEngineAdapter,
            strategy_class=prepared.strategy_class,
            bars=prepared.bars,
            config=prepared.config,
            params=prepared.params,
            provider=prepared.provider,
            effective_pre_bars=(
                prepared.effective_pre_bars if prepared.effective_pre_bars else None
            ),
            console=console,
            perf_counter=perf_counter,
        )
    except Exception as exc:
        registry.update_status(strategy_id, "error")
        console.print(f"[red]Backtest failed: {type(exc).__name__}: {exc}[/red]")
        sys.exit(1)


def _build_cli_bar_query(
    *,
    symbol: str,
    exchange: str,
    market_type: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    bar_query_cls,
    instrument_key_cls,
    parse_timeframe_func,
    gap_policy: str = "fail",
):
    return bar_query_cls(
        instrument=instrument_key_cls(
            symbol=symbol.upper(),
            exchange=exchange.upper(),
            market=market_type.lower(),
        ),
        timeframe=parse_timeframe_func(timeframe),
        start_ms=start_ms,
        end_ms=end_ms,
        gap_policy=gap_policy,
    )


def _build_indicator_plot_config(
    *,
    symbol: str,
    timeframe: str,
    exchange: str,
    market_type: str,
    provider,
):
    return SimpleNamespace(
        symbol=symbol,
        timeframe=timeframe,
        parity_mode=None,
        process_orders_on_close=None,
        calc_on_order_fills=None,
        calc_on_every_tick=None,
        mintick=0.01,
        currency="USD",
        data_provider=getattr(provider, "_provider", None),
        exchange=exchange.lower(),
        market_type=market_type.lower(),
    )


def _build_indicator_plot_run_meta(
    *,
    name: str,
    source,
    symbol: str,
    exchange: str,
    market_type: str,
    timeframe: str,
    start_ms: int,
    end_ms: int,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
    bars_total: int,
    data_fetch_info,
    plots_rows: int,
    timings: dict[str, float],
    plots_csv: Path,
) -> dict:
    return {
        "type": "indicator",
        "pine_name": name,
        "pine_id": source.id,
        "artifact_id": source.active_artifact_id,
        "symbol": symbol,
        "exchange": exchange,
        "market_type": market_type,
        "timeframe": timeframe,
        "calculation_from": start_ms,
        "calculation_to": end_ms,
        "compare_from": compare_from_ms,
        "compare_to": compare_to_ms,
        "bars_total": bars_total,
        "data_fetch": data_fetch_info,
        "plots_rows": plots_rows,
        "timings": timings,
        "outputs": {"plots": str(plots_csv)},
    }


__all__ = [
    "_fmt_utc_ms",
    "_fmt_utc_seconds",
    "_fmt_utc_ms_as",
    "_default_qty_step",
    "_default_qty_rounding_mode",
    "_parse_cli_date_ms",
    "_plot_record_count",
    "_bars_data_fingerprint",
    "_build_strategy_backtest_config",
    "_build_strategy_replay_config",
    "_get_strategy_or_exit",
    "_print_strategy_command_header",
    "_strategy_backtest_readiness_error",
    "_exit_if_strategy_not_ready_for_backtest",
    "_parse_strategy_backtest_window",
    "_parse_valid_strategy_backtest_window",
    "_print_backtest_result_summary",
    "_load_strategy_backtest_class",
    "_load_strategy_backtest_class_or_exit",
    "_exit_if_no_strategy_bars",
    "_build_strategy_backtest_params_and_config",
    "_prepare_strategy_backtest_inputs",
    "_prepare_strategy_replay_inputs",
    "_build_strategy_backtest_run_request",
    "_prepare_strategy_backtest_runtime",
    "_build_progress_callback",
    "_parse_indicator_plot_window",
    "_load_pine_source_or_exit",
    "_require_active_pine_artifact",
    "_load_generated_class_timed",
    "_print_indicator_plot_header",
    "_ensure_output_dir",
    "_strategy_backtest_dependencies",
    "_indicator_plot_dependencies",
    "_load_indicator_plot_bars",
    "_execute_indicator_plot_runtime",
    "_run_indicator_plot_runtime",
    "_write_indicator_plot_outputs",
    "_write_indicator_plot_run_meta",
    "_write_indicator_plot_run_outputs",
    "_prepare_indicator_plot_inputs",
    "_build_strategy_backtest_run_meta",
    "_print_strategy_plot_capture_status",
    "_load_strategy_backtest_bars",
    "_strategy_backtest_declaration_args",
    "_save_strategy_backtest_result",
    "_save_strategy_backtest_result_safely",
    "_persist_strategy_backtest_result",
    "_save_strategy_resume_snapshot",
    "_run_strategy_backtest_adapter",
    "_run_strategy_backtest_or_exit",
    "_build_cli_bar_query",
    "_build_indicator_plot_config",
    "_build_indicator_plot_run_meta",
]
