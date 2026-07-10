"""TradingView parity lab routes and helpers.

This module is intentionally a real vertical slice, not a UI placeholder:
TradingView candle CSVs are parsed into marketdata-provider Bar objects, the
normal OpenPine backtest engine is run against those bars, normalized OpenPine
CSV exports are produced, and optional TradingView chart/trades/equity exports
are compared into downloadable reports.
"""

from __future__ import annotations

import csv
import json
import math
import shutil
import time
import uuid
from dataclasses import dataclass
from functools import partial
from pathlib import Path
from typing import Any

from fastapi import (
    APIRouter,
    BackgroundTasks,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    UploadFile,
)
from fastapi.responses import FileResponse, Response

from openpine._compat import structlog
from openpine.cli.compare import _compare_csv_float, _compare_csv_time_ms
from openpine.export import ExportWindow, export_strategy_result
from openpine.gateway.deps import GatewayState, get_state
from openpine.gateway.routes.backtest import (
    _bar_series_fingerprint,
    _run_backtest_in_process,
    _save_backtest_data_fingerprint,
)
from openpine.gateway.ws_manager import ws_manager

log = structlog.get_logger(__name__)
router = APIRouter(prefix="/tv-parity", tags=["tv-parity"])


@dataclass(frozen=True, slots=True)
class ParsedTradingViewCandles:
    """Parsed TradingView candle CSV plus metadata for API previews."""

    bars: tuple[Any, ...]
    series: Any
    summary: dict[str, Any]


_TIME_COLUMNS = (
    "time",
    "timestamp",
    "date/time",
    "date and time",
    "date",
    "datetime",
    "open time",
    "open_time",
    "bar_time",
    "bar_time_ms",
    "time_ms",
)
_CANDLE_COLUMNS = {
    "open": ("open", "o"),
    "high": ("high", "h"),
    "low": ("low", "l"),
    "close": ("close", "c"),
    "volume": ("volume", "vol", "v"),
}
_ARTIFACT_RELATIVE_PATHS = {
    "openpine_plots": Path("openpine_outputs/plots.csv"),
    "openpine_trades": Path("openpine_outputs/trades.csv"),
    "openpine_all_trades": Path("openpine_outputs/all_trades.csv"),
    "openpine_equity": Path("openpine_outputs/equity_curve.csv"),
    "comparison_csv": Path("comparison/comparison_summary.csv"),
    "comparison_json": Path("comparison/comparison_summary.json"),
    "comparison_report": Path("comparison/comparison_report.md"),
    "tv_trades_normalized": Path("comparison/tradingview_trades_normalized.csv"),
    "uploaded_candles": Path("uploads/candles.csv"),
    "uploaded_chart": Path("uploads/chart.csv"),
    "uploaded_trades": Path("uploads/trades.csv"),
    "uploaded_equity": Path("uploads/equity.csv"),
    "request_json": Path("request.json"),
    "result_json": Path("tv_parity_result.json"),
}
_MAX_TV_PARITY_UPLOAD_BYTES = 25 * 1024 * 1024


def _normalize_header(value: str) -> str:
    return " ".join(str(value).strip().lower().replace("\ufeff", "").split())


def _find_csv_column(fields: list[str], candidates: tuple[str, ...]) -> str | None:
    normalized = {field: _normalize_header(field).replace("_", " ") for field in fields}
    candidate_set = {_normalize_header(item).replace("_", " ") for item in candidates}
    for field, header in normalized.items():
        if header in candidate_set:
            return field
    return None


def _required_candle_columns(fields: list[str]) -> dict[str, str]:
    found = {
        name: _find_csv_column(fields, candidates)
        for name, candidates in _CANDLE_COLUMNS.items()
    }
    time_col = _find_csv_column(fields, _TIME_COLUMNS)
    if time_col is not None:
        found["time"] = time_col
    missing = [name for name in ("time", "open", "high", "low", "close") if not found.get(name)]
    if missing:
        raise ValueError(f"missing required candle columns: {', '.join(missing)}")
    return {name: column for name, column in found.items() if column is not None}


def _parse_required_float(row: dict[str, str], column: str) -> float | None:
    value = _compare_csv_float(row.get(column))
    return None if math.isnan(value) else value


def parse_tradingview_candles_csv(
    path: str | Path,
    *,
    exchange: str,
    market_type: str,
    symbol: str,
    timeframe: str,
    from_ms: int | None = None,
    to_ms: int | None = None,
    drop_pre_window: bool = True,
) -> ParsedTradingViewCandles:
    """Parse a TradingView candle CSV into a BarSeries for replay backtests."""

    from marketdata_provider.contracts import (
        Bar,
        BarQuery,
        BarSeries,
        CoverageReport,
        InstrumentKey,
        parse_timeframe,
    )

    source = Path(path)
    with source.open(newline="", encoding="utf-8-sig", errors="replace") as handle:
        reader = csv.DictReader(handle)
        fields = list(reader.fieldnames or [])
        columns = _required_candle_columns(fields)
        timeframe_obj = parse_timeframe(timeframe)
        duration_ms = timeframe_obj.duration_ms
        if not duration_ms:
            raise ValueError(f"timeframe must have fixed duration for CSV replay: {timeframe}")
        instrument = InstrumentKey(
            exchange=exchange.lower(),
            market=market_type.lower(),
            symbol=symbol.upper(),
        )
        bars: list[Any] = []
        invalid_rows = 0
        total_rows = 0
        seen_times: set[int] = set()
        duplicate_times: list[int] = []
        for row in reader:
            total_rows += 1
            open_time = _compare_csv_time_ms(row.get(columns["time"]))
            if open_time is None:
                invalid_rows += 1
                continue
            time_close = open_time + duration_ms
            if from_ms is not None and drop_pre_window and open_time < from_ms:
                continue
            if to_ms is not None and open_time >= to_ms:
                continue
            ohlc = {
                key: _parse_required_float(row, columns[key])
                for key in ("open", "high", "low", "close")
            }
            if any(value is None for value in ohlc.values()):
                invalid_rows += 1
                continue
            volume = None
            if volume_col := columns.get("volume"):
                volume_value = _compare_csv_float(row.get(volume_col))
                volume = None if math.isnan(volume_value) else volume_value
            if open_time in seen_times:
                duplicate_times.append(open_time)
            seen_times.add(open_time)
            bars.append(
                Bar(
                    instrument=instrument,
                    timeframe=timeframe_obj,
                    time=open_time,
                    time_close=time_close,
                    open=float(ohlc["open"]),
                    high=float(ohlc["high"]),
                    low=float(ohlc["low"]),
                    close=float(ohlc["close"]),
                    volume=volume,
                    closed=True,
                )
            )
    bars.sort(key=lambda bar: bar.time)
    if not bars:
        raise ValueError("no valid TradingView candle rows found")
    query = BarQuery(
        instrument=bars[0].instrument,
        timeframe=bars[0].timeframe,
        start_ms=bars[0].time,
        end_ms=bars[-1].time_close,
        gap_policy="allow_with_metadata",
    )
    coverage = CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=bars[-1].time_close,
        duplicate_timestamps=tuple(duplicate_times),
        source_mix=("tradingview_csv",),
    )
    series = BarSeries(query, tuple(bars), coverage)
    summary = {
        "source": "tradingview_csv",
        "filename": source.name,
        "exchange": exchange.lower(),
        "market_type": market_type.lower(),
        "symbol": symbol.upper(),
        "timeframe": timeframe,
        "columns": fields,
        "total_rows": total_rows,
        "valid_bars": len(bars),
        "invalid_rows": invalid_rows,
        "duplicate_timestamps": len(duplicate_times),
        "from_time": bars[0].time,
        "to_time": bars[-1].time_close,
    }
    return ParsedTradingViewCandles(bars=tuple(bars), series=series, summary=summary)


def _normalize_tv_parity_source(source: str | None) -> str:
    normalized = (source or "tradingview_csv").strip().lower().replace("-", "_")
    aliases = {
        "csv": "tradingview_csv",
        "tv_csv": "tradingview_csv",
        "tradingview": "tradingview_csv",
        "tradingview_csv": "tradingview_csv",
        "exchange": "exchange_data",
        "exchange_data": "exchange_data",
        "marketdata": "exchange_data",
        "provider": "exchange_data",
    }
    if normalized not in aliases:
        raise HTTPException(400, f"Unsupported TV parity source: {source}")
    return aliases[normalized]


def _fixed_timeframe_duration_ms(timeframe: str) -> int:
    from marketdata_provider.contracts import parse_timeframe

    duration_ms = parse_timeframe(timeframe).duration_ms
    if not duration_ms:
        raise HTTPException(400, f"timeframe must have fixed duration for exchange-data replay: {timeframe}")
    return int(duration_ms)


def _exchange_data_window(
    *,
    strategy: Any,
    requested_from_ms: int | None,
    requested_to_ms: int | None,
    compare_from_ms: int | None,
    compare_to_ms: int | None,
    warmup_bars: int,
    full_prehistory: bool,
) -> tuple[int, int, int, int]:
    visible_from = compare_from_ms if compare_from_ms is not None else requested_from_ms
    visible_to = compare_to_ms if compare_to_ms is not None else requested_to_ms
    if visible_from is None or visible_to is None:
        raise HTTPException(400, "exchange_data source requires compare_from_time/from_time and compare_to_time/to_time")
    if visible_from >= visible_to:
        raise HTTPException(400, "compare_from_time must be before compare_to_time")
    calculation_to = requested_to_ms if requested_to_ms is not None else visible_to
    if calculation_to < visible_to:
        raise HTTPException(400, "to_time must be at or after compare_to_time")
    if full_prehistory:
        calculation_from = requested_from_ms if requested_from_ms is not None else 0
    elif warmup_bars > 0:
        calculation_from = max(0, visible_from - warmup_bars * _fixed_timeframe_duration_ms(strategy.timeframe))
    else:
        calculation_from = visible_from
    if calculation_from > visible_from:
        raise HTTPException(400, "from_time must be at or before compare_from_time")
    if calculation_from >= calculation_to:  # pragma: no cover - defensive after visible-window ordering checks
        raise HTTPException(400, "from_time must be before to_time")
    return calculation_from, calculation_to, visible_from, visible_to


def load_exchange_data_candles(
    *,
    state: GatewayState,
    strategy: Any,
    calculation_from_ms: int,
    calculation_to_ms: int,
    compare_from_ms: int,
    compare_to_ms: int,
    full_prehistory: bool,
) -> ParsedTradingViewCandles:
    from marketdata_provider.contracts import BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

    instrument = InstrumentKey(
        exchange=strategy.exchange.lower(),
        market=strategy.market_type.lower(),
        symbol=strategy.symbol.upper(),
    )
    query = BarQuery(
        instrument=instrument,
        timeframe=parse_timeframe(strategy.timeframe),
        start_ms=calculation_from_ms,
        end_ms=calculation_to_ms,
        gap_policy="allow_with_metadata",
    )
    orchestrator = getattr(state, "orchestrator", None)
    if orchestrator is None:
        raise HTTPException(500, "exchange data provider is unavailable")
    series = orchestrator.load_bars(query)
    bars = tuple(series.bars)
    if not bars:
        raise HTTPException(400, "no exchange bars found for requested TV parity window")
    effective_pre_bars = sum(1 for bar in bars if int(bar.time) < compare_from_ms)
    delivered_from = int(bars[0].time)
    delivered_to = int(getattr(bars[-1], "time_close", bars[-1].time))
    coverage = CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=delivered_from,
        delivered_end_ms=delivered_to,
        source_mix=("exchange_data",),
    )
    parsed_series = BarSeries(query, bars, coverage)
    summary = {
        "source": "exchange_data",
        "exchange": strategy.exchange.lower(),
        "market_type": strategy.market_type.lower(),
        "symbol": strategy.symbol.upper(),
        "timeframe": strategy.timeframe,
        "total_rows": len(bars),
        "valid_bars": len(bars),
        "invalid_rows": 0,
        "duplicate_timestamps": 0,
        "from_time": delivered_from,
        "to_time": delivered_to,
        "calculation_from": calculation_from_ms,
        "calculation_to": calculation_to_ms,
        "compare_from": compare_from_ms,
        "compare_to": compare_to_ms,
        "full_prehistory": full_prehistory,
        "effective_pre_bars": effective_pre_bars,
    }
    return ParsedTradingViewCandles(bars=bars, series=parsed_series, summary=summary)


def _safe_upload_filename(filename: str | None, fallback: str) -> str:
    raw = Path(filename or fallback).name.replace("\\", "_")
    stem = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in raw)
    if not stem or stem in {".", ".."}:
        stem = fallback
    return stem


def _tv_parity_root(state: GatewayState) -> Path:
    root = Path(state.config.data_dir).expanduser() / "tv-parity"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _run_root(state: GatewayState, run_id: str) -> Path:
    safe = _safe_upload_filename(run_id, "run")
    return _tv_parity_root(state) / safe


def _is_seeded_demo_run_id(run_id: Any) -> bool:
    return isinstance(run_id, str) and run_id.startswith("tvpar_demo_")


def _list_tv_parity_history(state: GatewayState) -> list[dict[str, Any]]:
    """Scan data_dir/tv-parity/ for run directories and read their summary JSON.

    Returns one entry per run, newest first. Reads only ``tv_parity_result.json``
    (the same file the ``GET /runs/{id}`` endpoint returns), so this stays in
    sync with the per-run payload contract automatically.
    """
    root = _tv_parity_root(state)
    entries: list[dict[str, Any]] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue
        result_path = child / "tv_parity_result.json"
        if not result_path.exists() or not result_path.is_file():
            continue
        try:
            payload = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        candle_summary = payload.get("candle_summary") or {}
        run_id = payload.get("run_id") or child.name
        entries.append(
            {
                "run_id": run_id,
                "strategy_id": payload.get("strategy_id"),
                "source": payload.get("source"),
                "status": payload.get("status"),
                "queued_at": payload.get("queued_at"),
                "compare_from": payload.get("compare_from"),
                "compare_to": payload.get("compare_to"),
                "symbol": candle_summary.get("symbol"),
                "exchange": candle_summary.get("exchange"),
                "market_type": candle_summary.get("market_type"),
                "timeframe": candle_summary.get("timeframe"),
                "valid_bars": candle_summary.get("valid_bars"),
                "from_time": candle_summary.get("from_time"),
                "to_time": candle_summary.get("to_time"),
                "is_demo": _is_seeded_demo_run_id(run_id),
                "result_path": result_path.as_posix(),
            }
        )
    entries.sort(
        key=lambda entry: entry.get("queued_at") or 0,
        reverse=True,
    )
    return entries


def _path_is_under(path: Path, root: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(root.resolve(strict=False))
    except ValueError:
        return False
    return True


async def _store_upload(
    upload: UploadFile | None,
    *,
    upload_root: Path,
    fallback_name: str,
    max_bytes: int = _MAX_TV_PARITY_UPLOAD_BYTES,
) -> Path | None:
    if upload is None:
        return None
    upload_root.mkdir(parents=True, exist_ok=True)
    suffix = Path(upload.filename or fallback_name).suffix or Path(fallback_name).suffix
    target = upload_root / fallback_name
    if suffix and target.suffix != suffix:
        target = target.with_suffix(suffix)
    target = upload_root / _safe_upload_filename(target.name, fallback_name)
    written = 0
    try:
        with target.open("wb") as handle:
            while True:
                chunk = upload.file.read(1024 * 1024)
                if not chunk:
                    break
                written += len(chunk)
                if written > max_bytes:
                    raise HTTPException(413, f"Upload exceeds {max_bytes} byte limit")
                handle.write(chunk)
    except Exception:
        target.unlink(missing_ok=True)
        raise
    finally:
        await upload.close()
    return target


def _artifact_catalog(run_id: str, run_root: Path) -> list[dict[str, Any]]:
    artifacts = []
    for name, relative in _ARTIFACT_RELATIVE_PATHS.items():
        path = run_root / relative
        if path.exists() and path.is_file():
            artifacts.append(
                {
                    "name": name,
                    "filename": path.name,
                    "size_bytes": path.stat().st_size,
                    "download_url": f"/api/tv-parity/runs/{run_id}/artifacts/{name}",
                }
            )
    return artifacts


def _public_path(value: str | Path | None, root: Path) -> str | None:
    if value is None:
        return None
    path = Path(value)
    if not path.is_absolute():
        return path.as_posix()
    try:
        return path.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return path.name


def _public_path_map(values: dict[str, Any], root: Path) -> dict[str, str | None]:
    return {key: _public_path(value, root) for key, value in values.items()}


def _public_payload_paths(value: Any, root: Path) -> Any:
    if isinstance(value, dict):
        return {key: _public_payload_paths(item, root) for key, item in value.items()}
    if isinstance(value, list):
        return [_public_payload_paths(item, root) for item in value]
    if isinstance(value, tuple):
        return tuple(_public_payload_paths(item, root) for item in value)
    if isinstance(value, str) and Path(value).is_absolute():
        return _public_path(value, root)
    return value


def write_tv_parity_exports_and_comparison(
    *,
    strategy_id: str,
    run_id: str,
    raw_result: Any,
    output_root: str | Path,
    compare_from_ms: int,
    compare_to_ms: int,
    tv_chart_path: str | Path | None,
    tv_trades_path: str | Path | None,
    tv_equity_path: str | Path | None,
    abs_tol: float,
    rel_tol: float,
    include_base_columns: bool,
) -> dict[str, Any]:
    """Export OpenPine outputs and compare them against optional TV CSV exports."""

    from openpine.cli.compare import _compare_strategy_run_with_tv_exports

    root = Path(output_root)
    openpine_output_dir = root / "openpine_outputs"
    comparison_dir = root / "comparison"
    export_result = export_strategy_result(
        result=raw_result,
        window=ExportWindow(compare_from_ms, compare_to_ms),
        output_dir=openpine_output_dir,
    )
    exported = dict(export_result.outputs)
    if "equity_curve" in exported:
        exported["equity"] = exported["equity_curve"]
    comparison = None
    if tv_chart_path or tv_trades_path or tv_equity_path:
        comparison = _compare_strategy_run_with_tv_exports(
            strategy_id=strategy_id,
            run=type("RunRef", (), {"run_id": run_id})(),
            exported=exported,
            output_path=comparison_dir,
            tv_chart=str(tv_chart_path) if tv_chart_path else None,
            tv_trades=str(tv_trades_path) if tv_trades_path else None,
            tv_equity=str(tv_equity_path) if tv_equity_path else None,
            abs_tol=abs_tol,
            rel_tol=rel_tol,
            include_base_columns=include_base_columns,
            compare_from_ms=compare_from_ms,
            compare_to_ms=compare_to_ms,
        )
    return {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "export_format": "openpine_normalized_tv_like_v1",
        "compare_from": compare_from_ms,
        "compare_to": compare_to_ms,
        "outputs": _public_path_map(export_result.outputs, root),
        "rows": {
            "plots": export_result.plots_rows,
            "trades": export_result.trades_rows,
            "all_trades": export_result.all_trades_rows,
            "equity": export_result.equity_rows,
        },
        "initial_equity_at_export_start": export_result.initial_equity_at_export_start,
        "comparison": _public_payload_paths(comparison, root),
    }


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def _strategy_decl_args(state: GatewayState, strategy: Any) -> dict[str, Any]:
    from openpine.runtime.declaration_args import artifact_strategy_declaration_args

    try:
        artifact = state.artifact_store.get_artifact(strategy.artifact_id, strategy.pine_id)
        return artifact_strategy_declaration_args(artifact)
    except Exception:
        return artifact_strategy_declaration_args(None)


def _backtest_config_for_tv_replay(
    *,
    strategy: Any,
    from_ms: int,
    to_ms: int,
    warmup_bars: int,
    capture_plots: bool,
    decl_args: dict[str, Any],
    compare_from_ms: int | None = None,
    compare_to_ms: int | None = None,
    effective_pre_bars: int = 0,
) -> Any:
    from openpine.runtime.engine import BacktestRunConfig
    from openpine.runtime.declaration_args import normalize_strategy_declaration_args
    from openpine.exchange_metadata import (
        default_price_tick,
        default_qty_rounding_mode,
        default_qty_step,
    )

    decl_args = normalize_strategy_declaration_args(decl_args)
    commission_type = {
        "cash_per_order": "fixed_per_order",
        "cash_per_contract": "fixed_per_contract",
    }.get(str(decl_args.get("commission_type", "none")), decl_args.get("commission_type", "none"))
    visible_from_ms = compare_from_ms if effective_pre_bars > 0 and compare_from_ms is not None else from_ms
    visible_to_ms = compare_to_ms if compare_to_ms is not None else to_ms
    max_pre_bars = max(int(warmup_bars or 0), int(effective_pre_bars or 0))
    return BacktestRunConfig(
        symbol=strategy.symbol,
        timeframe=strategy.timeframe,
        start_time=visible_from_ms,
        end_time=visible_to_ms,
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
        process_orders_on_close=bool(decl_args.get("process_orders_on_close", False)),
        calc_on_order_fills=bool(decl_args.get("calc_on_order_fills", False)),
        calc_on_every_tick=bool(decl_args.get("calc_on_every_tick", False)),
        use_bar_magnifier=bool(decl_args.get("use_bar_magnifier", False)),
        qty_step=default_qty_step(strategy.exchange, strategy.market_type, strategy.symbol),
        qty_rounding_mode=default_qty_rounding_mode(
            strategy.exchange, strategy.market_type, strategy.symbol
        ),
        mintick=default_price_tick(strategy.exchange, strategy.market_type, strategy.symbol) or 0.01,
        export_resume_state=False,
        content_hash_enabled=True,
        collect_events=True,
        collect_order_lifecycle=True,
        max_bars_back=max_pre_bars,
        score_start_time=visible_from_ms if effective_pre_bars > 0 else None,
        score_end_time=visible_to_ms if effective_pre_bars > 0 else None,
        max_pre_bars=max_pre_bars,
        warmup_metadata=(
            {"recommended_pre_bars_raw": max_pre_bars} if max_pre_bars > 0 else None
        ),
        plot_from_ms=compare_from_ms if capture_plots else None,
        plot_to_ms=compare_to_ms if capture_plots else None,
        capture_plots=capture_plots,
    )


async def _run_tv_parity_background(
    *,
    state: GatewayState,
    strategy_id: str,
    run_id: str,
    parsed: ParsedTradingViewCandles,
    run_root: Path,
    uploads: dict[str, str | None],
    params_override: dict[str, Any] | None,
    warmup_bars: int,
    capture_plots: bool,
    compare_from_ms: int,
    compare_to_ms: int,
    abs_tol: float,
    rel_tol: float,
    include_base_columns: bool,
) -> None:
    """Execute TV-candle replay and optional comparison in the background."""

    source = str(parsed.summary.get("source", "tradingview_csv"))
    effective_pre_bars = int(parsed.summary.get("effective_pre_bars", 0) or 0)
    try:
        import asyncio

        strategy = state.strategy_registry.get_strategy(strategy_id)
        ws_manager.update_progress(
            run_id,
            "backtest",
            "running",
            0.05,
            "Loading strategy artifact for TradingView replay...",
        )
        await ws_manager.broadcast_progress(run_id)

        from openpine.runtime.engine import BacktestArtifactError, BacktestEngineAdapter, load_strategy_class_from_artifact

        try:
            strategy_class = load_strategy_class_from_artifact(
                strategy.pine_id,
                strategy.artifact_id,
                symbol=strategy.symbol,
                timeframe=strategy.timeframe,
            )
        except BacktestArtifactError as exc:
            state.backtest_store.mark_failed(run_id, str(exc))
            failure = {
                "run_id": run_id,
                "strategy_id": strategy_id,
                "status": "failed",
                "source": source,
                "candle_summary": parsed.summary,
                "uploads": _public_path_map(uploads, run_root),
                "error": str(exc),
                "artifacts": _artifact_catalog(run_id, run_root),
            }
            _write_json(run_root / "tv_parity_result.json", failure)
            ws_manager.update_progress(run_id, "backtest", "failed", 0.0, str(exc))
            await ws_manager.broadcast_progress(run_id)
            return

        fingerprint = _bar_series_fingerprint(parsed.series)
        _save_backtest_data_fingerprint(state, run_id, fingerprint)
        ws_manager.update_progress(
            run_id,
            "backtest",
            "running",
            0.2,
            f"Running replay on {len(parsed.bars):,} TradingView candles...",
            detail={
                "phase": "tv_candle_replay",
                "bars_processed": 0,
                "total_bars": len(parsed.bars),
                "data_fingerprint": fingerprint,
                "source": source,
            },
        )
        await ws_manager.broadcast_progress(run_id)

        params = params_override
        if params is None and getattr(strategy, "params_json", None):
            params = json.loads(strategy.params_json)
        config = _backtest_config_for_tv_replay(
            strategy=strategy,
            from_ms=parsed.summary["from_time"],
            to_ms=parsed.summary["to_time"],
            warmup_bars=warmup_bars,
            capture_plots=capture_plots,
            decl_args=_strategy_decl_args(state, strategy),
            compare_from_ms=compare_from_ms,
            compare_to_ms=compare_to_ms,
            effective_pre_bars=effective_pre_bars,
        )
        runtime_data_provider = None
        try:
            from openpine.data.provider_adapter import create_local_runtime_data_provider_adapter

            runtime_data_provider = create_local_runtime_data_provider_adapter(
                cache_dir=(state.config.data_cache_root or (state.config.data_dir / "cache")) / "marketdata",
                exchange=config.exchange,
                market=config.market_type,
                prefetch_end_ms=parsed.summary["to_time"],
            )
        except Exception as exc:
            log.warning("tv_parity_runtime_provider_init_failed", run_id=run_id, error=str(exc))

        def progress_callback(done: int, total: int) -> None:
            ws_manager.update_progress(
                run_id,
                "backtest",
                "running",
                0.2 + 0.6 * (done / max(total, 1)),
                f"TV replay bars: {done}/{total}",
                detail={"phase": "compute", "bars_processed": done, "total_bars": total},
            )

        run_effective_pre_bars = effective_pre_bars if effective_pre_bars > 0 else None
        run_args = (
            BacktestEngineAdapter(),
            strategy_class,
            list(parsed.bars),
            config,
            params or {},
            runtime_data_provider,
            progress_callback,
        )
        run_callable = (
            partial(_run_backtest_in_process, *run_args, run_effective_pre_bars)
            if run_effective_pre_bars is not None
            else partial(_run_backtest_in_process, *run_args)
        )

        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(None, run_callable)
        raw_result = result.raw_result
        state.backtest_store.save_result(
            run_id=run_id,
            result=raw_result,
            trades=getattr(raw_result, "trades", []) or [],
            equity_curve=getattr(raw_result, "equity_curve", None),
            plots=getattr(raw_result, "plots", None) if capture_plots else None,
        )
        payload = write_tv_parity_exports_and_comparison(
            strategy_id=strategy_id,
            run_id=run_id,
            raw_result=raw_result,
            output_root=run_root,
            compare_from_ms=compare_from_ms,
            compare_to_ms=compare_to_ms,
            tv_chart_path=uploads.get("chart"),
            tv_trades_path=uploads.get("trades"),
            tv_equity_path=uploads.get("equity"),
            abs_tol=abs_tol,
            rel_tol=rel_tol,
            include_base_columns=include_base_columns,
        )
        payload.update(
            {
                "status": "done",
                "source": source,
                "candle_summary": parsed.summary,
                "uploads": _public_path_map(uploads, run_root),
                "artifacts": _artifact_catalog(run_id, run_root),
                "bars_processed": getattr(result, "bars_processed", len(parsed.bars)),
            }
        )
        _write_json(run_root / "tv_parity_result.json", payload)
        ws_manager.update_progress(
            run_id,
            "backtest",
            "completed",
            1.0,
            "TradingView parity replay completed.",
            detail={
                "phase": "completed",
                "bars_processed": getattr(result, "bars_processed", len(parsed.bars)),
                "total_bars": len(parsed.bars),
                "data_fingerprint": fingerprint,
                "source": source,
            },
        )
        await ws_manager.broadcast_progress(run_id)
    except Exception as exc:
        log.error("tv_parity_failed", run_id=run_id, error=str(exc))
        try:
            state.backtest_store.mark_failed(run_id, str(exc))
        except Exception:
            pass
        failure = {
            "run_id": run_id,
            "strategy_id": strategy_id,
            "status": "failed",
            "source": source,
            "candle_summary": parsed.summary,
            "uploads": _public_path_map(uploads, run_root),
            "error": str(exc),
            "artifacts": _artifact_catalog(run_id, run_root),
        }
        _write_json(run_root / "tv_parity_result.json", failure)
        ws_manager.update_progress(run_id, "backtest", "failed", 0.0, str(exc))
        await ws_manager.broadcast_progress(run_id)


@router.get("/runs")
async def list_tv_parity_runs(
    state: GatewayState = Depends(get_state),
    strategy_id: str | None = Query(None, description="Filter by strategy_id"),
    source: str | None = Query(None, description="Filter by source (tradingview_csv | exchange_data)"),
    include_demo: bool = Query(False, description="Include seeded demo rows such as tvpar_demo_*"),
    limit: int = Query(50, ge=1, le=500, description="Maximum number of runs to return"),
) -> dict[str, Any]:
    """List TV parity runs discovered on disk, newest first.

    The history is reconstructed from ``data_dir/tv-parity/{run_id}/tv_parity_result.json``
    files written by ``POST /run``. Each entry includes the same fields surfaced
    by the per-run detail endpoint so the UI can render a list without making
    one request per row.
    """
    entries = _list_tv_parity_history(state)
    if not include_demo:
        entries = [e for e in entries if not e.get("is_demo")]
    if strategy_id is not None and strategy_id != "":
        entries = [e for e in entries if e.get("strategy_id") == strategy_id]
    if source is not None and source != "":
        normalized = _normalize_tv_parity_source(source)
        entries = [e for e in entries if e.get("source") == normalized]
    total = len(entries)
    entries = entries[:limit]
    return {
        "items": entries,
        "total": total,
        "limit": limit,
        "strategy_id": strategy_id,
        "source": source,
        "include_demo": include_demo,
    }


@router.post("/preview-candles")
async def preview_candles(
    candles_file: UploadFile = File(...),
    exchange: str = Form("binance"),
    market_type: str = Form("spot"),
    symbol: str = Form(...),
    timeframe: str = Form(...),
    state: GatewayState = Depends(get_state),
) -> dict[str, Any]:
    """Upload and preview a TradingView candle CSV before replay."""

    preview_id = f"preview_{uuid.uuid4().hex[:12]}"
    upload_root = _tv_parity_root(state) / preview_id / "uploads"
    stored = await _store_upload(
        candles_file,
        upload_root=upload_root,
        fallback_name="candles.csv",
    )
    if stored is None:
        raise HTTPException(400, "candles_file is required")
    try:
        parsed = parse_tradingview_candles_csv(
            stored,
            exchange=exchange,
            market_type=market_type,
            symbol=symbol,
            timeframe=timeframe,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        **parsed.summary,
        "locked_period": {
            "from_time": parsed.summary["from_time"],
            "to_time": parsed.summary["to_time"],
        },
    }


@router.post("/run")
async def run_tv_parity(
    background_tasks: BackgroundTasks,
    candles_file: UploadFile | None = File(None),
    strategy_id: str = Form(...),
    source: str = Form("tradingview_csv"),
    tv_chart_file: UploadFile | None = File(None),
    tv_trades_file: UploadFile | None = File(None),
    tv_equity_file: UploadFile | None = File(None),
    from_time: str | None = Form(None),
    to_time: str | None = Form(None),
    compare_from_time: str | None = Form(None),
    compare_to_time: str | None = Form(None),
    params_override_json: str | None = Form(None),
    warmup_bars: int = Form(0),
    full_prehistory: bool = Form(False),
    capture_plots: bool = Form(True),
    abs_tol: float = Form(1e-6),
    rel_tol: float = Form(1e-9),
    include_base_columns: bool = Form(False),
    state: GatewayState = Depends(get_state),
) -> dict[str, Any]:
    """Queue a real backtest replay using uploaded TradingView candles."""

    from openpine.storage.backtest_dto import BacktestRunRequest as StoreBacktestRunRequest
    from openpine.timezones import parse_timestamp_ms

    try:
        strategy = state.strategy_registry.get_strategy(strategy_id)
    except KeyError as exc:
        raise HTTPException(404, f"Strategy not found: {strategy_id}") from exc
    if not strategy.pine_id or not strategy.artifact_id:
        raise HTTPException(400, "Strategy has no pine_id or artifact_id. Compile first.")
    params_override = None
    if params_override_json:
        try:
            params_override = json.loads(params_override_json)
        except json.JSONDecodeError as exc:
            raise HTTPException(400, f"params_override_json is invalid JSON: {exc}") from exc
        if not isinstance(params_override, dict):
            raise HTTPException(400, "params_override_json must decode to an object")
    source = _normalize_tv_parity_source(source)
    requested_from = parse_timestamp_ms(from_time, 0) if from_time else None
    requested_to = parse_timestamp_ms(to_time, 0) if to_time else None
    requested_compare_from = parse_timestamp_ms(compare_from_time, 0) if compare_from_time else None
    requested_compare_to = parse_timestamp_ms(compare_to_time, 0) if compare_to_time else None
    pending_root = _tv_parity_root(state) / f"pending_{uuid.uuid4().hex[:16]}"
    pending_root.mkdir(parents=True, exist_ok=True)
    upload_root = pending_root / "uploads"
    candles_path = await _store_upload(candles_file, upload_root=upload_root, fallback_name="candles.csv")
    chart_path = await _store_upload(tv_chart_file, upload_root=upload_root, fallback_name="chart.csv")
    trades_path = await _store_upload(tv_trades_file, upload_root=upload_root, fallback_name="trades.csv")
    equity_path = await _store_upload(tv_equity_file, upload_root=upload_root, fallback_name="equity.csv")
    if source == "tradingview_csv":
        if candles_path is None:
            raise HTTPException(400, "candles_file is required")
        try:
            parsed = parse_tradingview_candles_csv(
                candles_path,
                exchange=strategy.exchange,
                market_type=strategy.market_type,
                symbol=strategy.symbol,
                timeframe=strategy.timeframe,
                from_ms=requested_from,
                to_ms=requested_to,
                drop_pre_window=False,
            )
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        compare_from_ms = (
            requested_compare_from
            if requested_compare_from is not None
            else requested_from
            if requested_from is not None
            else parsed.summary["from_time"]
        )
        compare_to_ms = (
            requested_compare_to
            if requested_compare_to is not None
            else requested_to
            if requested_to is not None
            else parsed.summary["to_time"]
        )
        effective_pre_bars = sum(1 for bar in parsed.bars if int(bar.time) < compare_from_ms)
        parsed.summary.update(
            {
                "calculation_from": parsed.summary["from_time"],
                "calculation_to": parsed.summary["to_time"],
                "compare_from": compare_from_ms,
                "compare_to": compare_to_ms,
                "requested_from_ms": requested_from,
                "requested_to_ms": requested_to,
                "effective_pre_bars": effective_pre_bars,
                "seeded_with_full_history": effective_pre_bars > 0,
            }
        )
    else:
        calculation_from_ms, calculation_to_ms, compare_from_ms, compare_to_ms = _exchange_data_window(
            strategy=strategy,
            requested_from_ms=requested_from,
            requested_to_ms=requested_to,
            compare_from_ms=requested_compare_from,
            compare_to_ms=requested_compare_to,
            warmup_bars=warmup_bars,
            full_prehistory=full_prehistory,
        )
        parsed = load_exchange_data_candles(
            state=state,
            strategy=strategy,
            calculation_from_ms=calculation_from_ms,
            calculation_to_ms=calculation_to_ms,
            compare_from_ms=compare_from_ms,
            compare_to_ms=compare_to_ms,
            full_prehistory=full_prehistory,
        )
    if compare_from_ms >= compare_to_ms:
        raise HTTPException(400, "compare_from_time must be before compare_to_time")
    run_id = state.backtest_store.create_run(
        StoreBacktestRunRequest(
            strategy_id=strategy_id,
            pine_id=strategy.pine_id,
            artifact_id=strategy.artifact_id,
            params_hash=strategy.params_hash,
            exchange=strategy.exchange,
            market_type=strategy.market_type,
            symbol=strategy.symbol,
            price_type=source,
            timeframe=strategy.timeframe,
            from_time=parsed.summary["from_time"],
            to_time=parsed.summary["to_time"],
            warmup_bars=warmup_bars,
        )
    )
    run_root = _run_root(state, run_id)
    if run_root.exists():
        shutil.rmtree(run_root)
    pending_root.rename(run_root)
    uploads = {
        "candles": str(run_root / "uploads" / Path(candles_path).name) if candles_path else None,
        "chart": str(run_root / "uploads" / Path(chart_path).name) if chart_path else None,
        "trades": str(run_root / "uploads" / Path(trades_path).name) if trades_path else None,
        "equity": str(run_root / "uploads" / Path(equity_path).name) if equity_path else None,
    }
    request_payload = {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "source": source,
        "queued_at": int(time.time() * 1000),
        "capture_plots": capture_plots,
        "warmup_bars": warmup_bars,
        "full_prehistory": full_prehistory,
        "abs_tol": abs_tol,
        "rel_tol": rel_tol,
        "include_base_columns": include_base_columns,
        "compare_from": compare_from_ms,
        "compare_to": compare_to_ms,
        "requested_from_ms": requested_from,
        "requested_to_ms": requested_to,
        "requested_compare_from_ms": requested_compare_from,
        "requested_compare_to_ms": requested_compare_to,
        "candle_summary": parsed.summary,
        "uploads": _public_path_map(uploads, run_root),
    }
    _write_json(run_root / "request.json", request_payload)
    _write_json(
        run_root / "tv_parity_result.json",
        {**request_payload, "status": "queued", "artifacts": _artifact_catalog(run_id, run_root)},
    )
    ws_manager.update_progress(
        run_id,
        "backtest",
        "queued",
        0.0,
        f"TV parity replay queued on {len(parsed.bars):,} candles.",
        detail={
            "phase": "queued",
            "bars_processed": 0,
            "total_bars": len(parsed.bars),
            "source": source,
            "locked_period": {
                "from_time": compare_from_ms,
                "to_time": compare_to_ms,
            },
        },
    )
    background_tasks.add_task(
        _run_tv_parity_background,
        state=state,
        strategy_id=strategy_id,
        run_id=run_id,
        parsed=parsed,
        run_root=run_root,
        uploads=uploads,
        params_override=params_override,
        warmup_bars=warmup_bars,
        capture_plots=capture_plots,
        compare_from_ms=compare_from_ms,
        compare_to_ms=compare_to_ms,
        abs_tol=abs_tol,
        rel_tol=rel_tol,
        include_base_columns=include_base_columns,
    )
    return {
        "run_id": run_id,
        "strategy_id": strategy_id,
        "status": "queued",
        "source": source,
        "locked_period": {
            "from_time": compare_from_ms,
            "to_time": compare_to_ms,
        },
        "valid_bars": len(parsed.bars),
        "artifacts_url": f"/api/tv-parity/runs/{run_id}",
    }


@router.get("/runs/{run_id}")
async def get_tv_parity_run(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, Any]:
    """Read TV parity result metadata and downloadable artifacts."""

    run_root = _run_root(state, run_id)
    result_path = run_root / "tv_parity_result.json"
    if not result_path.exists():
        raise HTTPException(404, f"TV parity run not found: {run_id}")
    try:
        payload = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise HTTPException(500, f"TV parity result is corrupt: {exc}") from exc
    payload["artifacts"] = _artifact_catalog(run_id, run_root)
    return payload


@router.delete("/runs/{run_id}", status_code=204)
async def delete_tv_parity_run(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> Response:
    """Delete a TV parity run directory and all of its artifacts from disk."""

    run_root = _run_root(state, run_id)
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(404, f"TV parity run not found: {run_id}")
    # Safety: ensure resolved path is under the tv-parity root.
    tv_root = _tv_parity_root(state)
    try:
        run_root.resolve(strict=False).relative_to(tv_root.resolve(strict=False))
    except ValueError as exc:  # pragma: no cover - defensive, _run_root always roots here
        raise HTTPException(400, f"Refusing to delete path outside tv-parity root: {run_id}") from exc
    shutil.rmtree(run_root)
    log.info("tv_parity_run_deleted", run_id=run_id, path=str(run_root))
    return Response(status_code=204)


@router.get("/runs/{run_id}/artifacts/{artifact_name}")
async def download_tv_parity_artifact(
    run_id: str,
    artifact_name: str,
    state: GatewayState = Depends(get_state),
) -> FileResponse:
    """Download a named TV parity artifact without exposing raw server paths."""

    relative = _ARTIFACT_RELATIVE_PATHS.get(artifact_name)
    if relative is None:
        raise HTTPException(404, f"Unknown TV parity artifact: {artifact_name}")
    run_root = _run_root(state, run_id)
    path = run_root / relative
    if not _path_is_under(path, run_root) or not path.exists() or not path.is_file():
        raise HTTPException(404, f"TV parity artifact not found: {artifact_name}")
    media_type = "text/csv" if path.suffix == ".csv" else "application/json"
    if path.suffix == ".md":
        media_type = "text/markdown"
    return FileResponse(path, media_type=media_type, filename=path.name)


# ---------------------------------------------------------------------------
# Visualization endpoints (chart data, top mismatches, diagnostics callouts,
# summary cards, HTML/PNG reports, ZIP export).
# ---------------------------------------------------------------------------


@router.get("/runs/{run_id}/chart-data")
async def get_tv_parity_chart_data(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, Any]:
    """Aligned equity / OHLC / signal series for the overlay canvas."""
    from openpine.gateway.routes import tv_parity_viz

    run_root = _run_root(state, run_id)
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(404, f"TV parity run not found: {run_id}")
    payload = tv_parity_viz.build_chart_data(run_root=run_root)
    payload["run_id"] = run_id
    return payload


@router.get("/runs/{run_id}/mismatches/top")
async def get_tv_parity_top_mismatches(
    run_id: str,
    limit: int = Query(20, ge=1, le=200),
    state: GatewayState = Depends(get_state),
) -> dict[str, Any]:
    """Top-N bars / trades by absolute delta (descending)."""
    from openpine.gateway.routes import tv_parity_viz

    run_root = _run_root(state, run_id)
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(404, f"TV parity run not found: {run_id}")
    return tv_parity_viz.top_mismatches(run_root=run_root, limit=limit)


@router.get("/runs/{run_id}/diagnostics/callouts")
async def get_tv_parity_diagnostics_callouts(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, Any]:
    """Collect ``P092_DIAG_*`` markers from the uploaded TV chart CSV."""
    from openpine.gateway.routes import tv_parity_viz

    run_root = _run_root(state, run_id)
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(404, f"TV parity run not found: {run_id}")
    payload = tv_parity_viz.diagnostics_callouts(run_root=run_root)
    payload["run_id"] = run_id
    return payload


@router.get("/runs/{run_id}/summary-cards")
async def get_tv_parity_summary_cards(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> dict[str, Any]:
    """Bloomberg-style compact status payload for the UI cards row."""
    from openpine.gateway.routes import tv_parity_viz

    run_root = _run_root(state, run_id)
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(404, f"TV parity run not found: {run_id}")
    return tv_parity_viz.summary_cards(run_root=run_root)


@router.get("/runs/{run_id}/report.html")
async def get_tv_parity_report_html(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> Response:
    """Render a self-contained HTML report for the run."""
    from openpine.gateway.routes import tv_parity_viz

    run_root = _run_root(state, run_id)
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(404, f"TV parity run not found: {run_id}")
    cards = tv_parity_viz.summary_cards(run_root=run_root)
    html = tv_parity_viz.render_html_report(
        run_root=run_root,
        run_id=run_id,
        strategy_id=cards.get("strategy_id"),
    )
    return Response(content=html, media_type="text/html")


@router.get("/runs/{run_id}/report.png")
async def get_tv_parity_report_png(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> Response:
    """Render a small PNG equity overlay (matplotlib, Agg backend)."""
    from openpine.gateway.routes import tv_parity_viz

    run_root = _run_root(state, run_id)
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(404, f"TV parity run not found: {run_id}")
    png_bytes = tv_parity_viz.render_png_report(run_root=run_root, run_id=run_id)
    return Response(content=png_bytes, media_type="image/png")


@router.get("/runs/{run_id}/export.zip")
async def get_tv_parity_export_zip(
    run_id: str,
    state: GatewayState = Depends(get_state),
) -> Response:
    """Bundle comparison artifacts + reports into a single zip."""
    from openpine.gateway.routes import tv_parity_viz

    run_root = _run_root(state, run_id)
    if not run_root.exists() or not run_root.is_dir():
        raise HTTPException(404, f"TV parity run not found: {run_id}")
    zip_bytes = tv_parity_viz.build_export_zip(run_root=run_root)
    headers = {"Content-Disposition": f'attachment; filename="tv-parity-{run_id}.zip"'}
    return Response(
        content=zip_bytes, media_type="application/zip", headers=headers
    )
