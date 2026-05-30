#!/usr/bin/env python3
"""Manifest-driven OpenPine batch runner for the clean TV export corpus.

The script is intentionally staged:

1. plan      - validate corpus/discovery only, no DB writes
2. ingest    - add Pine sources to OpenPine SQLite
3. compile   - ingest + compile Pine sources
4. register  - compile + create strategy instances for strategy exports
5. run       - compile/register + run each chart timeframe and write local outputs

Outputs are written inside each export folder:

    exports/<NNN_name>/openpine_outputs/<timeframe>/

This keeps OpenPine-generated artifacts separate from TradingView exports.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import importlib
import json
import math
import os
import re
import subprocess
import time
import traceback
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pandas as pd

DEFAULT_WORKSPACE = Path(os.environ.get("OPENPINE_WORKSPACE_ROOT", Path.cwd()))
LIBRARY_NAMES: tuple[str, ...] = (
    "openpine",
    "pine2ast",
    "ast2python",
    "pinelib",
    "backtest_engine",
    "marketdata_provider",
    "optimizer",
)


def _get_library_revisions() -> dict[str, str]:
    """Capture git revisions for all core libraries.

    This ensures run_meta.json records exactly which library state produced
    each output — required for reproducible replay.
    """
    result: dict[str, str] = {}
    for name in LIBRARY_NAMES:
        git_rev = "unknown"
        try:
            module = importlib.import_module(name)
            module_file = Path(getattr(module, "__file__", "")).resolve()
            repo_root = next(
                (candidate for candidate in [module_file.parent, *module_file.parents] if (candidate / ".git").exists()),
                None,
            )
            if repo_root is not None:
                git_rev = subprocess.check_output(
                    ["git", "rev-parse", "--short=8", "HEAD"],
                    cwd=str(repo_root),
                    timeout=5,
                ).decode().strip()
            else:
                git_rev = str(getattr(module, "__version__", "unknown"))
        except Exception:
            pass
        result[name] = git_rev
    return result


def _write_progress(
    root: Path,
    batch_id: str,
    entry_id: int | None,
    phase: str,
    status: str,
    note: str = "",
) -> None:
    """Write durable current_progress.json for operator visibility.

    This file is updated before and after each entry so operators can see
    exactly where a long batch is right now — not just the last stdout line.
    """
    payload = {
        "batch_id": batch_id,
        "phase": phase,
        "status": status,
        "note": note,
        "updated_at": utc_now(),
    }
    if entry_id is not None:
        payload["current_entry_id"] = entry_id
    write_json(root / "current_progress.json", payload)
CLEAN_ROOT = DEFAULT_WORKSPACE / "pine_oracle_1528_tv_exports_clean_20260529"
MANIFEST = CLEAN_ROOT / "manifest.csv"

BAR_CACHE: dict[tuple[str, str, str, str, int, int], list[Any]] = {}


@dataclass(frozen=True)
class ChartExport:
    timeframe: str
    path: Path
    bars: int
    start_ms: int
    end_ms: int


@dataclass(frozen=True)
class ExportEntry:
    export_id: int
    folder: str
    kind: str
    source_group: str
    root: Path
    pine_path: Path
    charts: tuple[ChartExport, ...]


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def ms_to_utc_iso(value: int | None) -> str | None:
    if value is None:
        return None
    return datetime.fromtimestamp(value / 1000, tz=timezone.utc).isoformat()


def sanitize_name(value: str, max_len: int = 96) -> str:
    value = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    value = re.sub(r"_+", "_", value).strip("_")
    return value[:max_len] or "unnamed"


def openpine_name(entry: ExportEntry) -> str:
    return sanitize_name(f"po_{entry.export_id:04d}_{entry.folder}", max_len=110)


def strategy_name(entry: ExportEntry, timeframe: str) -> str:
    return sanitize_name(f"{openpine_name(entry)}_{timeframe}", max_len=120)


def timeframe_from_name(path: Path) -> str | None:
    name = path.name.lower()
    if re.search(r"(^|[, _-])1d($|[, _.-])", name):
        return "1D"
    if re.search(r"(^|[, _-])15($|[, _.-])", name) or "15m" in name:
        return "15m"
    if re.search(r"(^|[, _-])60($|[, _.-])", name) or "1h" in name:
        return "1h"
    return None


def normalize_tf(value: str) -> str:
    lowered = value.lower()
    if lowered in {"15", "15m", "15min"}:
        return "15m"
    if lowered in {"60", "60m", "1h"}:
        return "1h"
    if lowered in {"1d", "d"}:
        return "1D"
    return value


def read_chart(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    required = {"time", "open", "high", "low", "close"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"{path.name}: missing OHLC columns {sorted(missing)}")
    if df.empty:
        raise ValueError(f"{path.name}: empty chart CSV")
    raw_time = pd.to_numeric(df["time"], errors="raise")
    if raw_time.max() > 2_000_000_000_000:
        bar_time = raw_time.astype("int64")
    elif raw_time.max() > 2_000_000_000:
        bar_time = raw_time.astype("int64")
    else:
        bar_time = (raw_time * 1000).astype("int64")
    df = df.copy()
    df["bar_time"] = bar_time
    return df


def infer_timeframe(path: Path, df: pd.DataFrame) -> str:
    by_name = timeframe_from_name(path)
    if by_name:
        return by_name
    diffs = df["bar_time"].diff().dropna()
    if not diffs.empty:
        mode = int(diffs.mode().iloc[0])
        if 850_000 <= mode <= 950_000:
            return "15m"
        if 3_500_000 <= mode <= 3_700_000:
            return "1h"
        if 82_000_000 <= mode <= 91_000_000:
            return "1D"
    raise ValueError(f"{path.name}: cannot infer timeframe")


def build_chart_export(path: Path) -> ChartExport:
    df = read_chart(path)
    tf = infer_timeframe(path, df)
    return ChartExport(
        timeframe=tf,
        path=path,
        bars=len(df),
        start_ms=int(df["bar_time"].min()),
        end_ms=int(df["bar_time"].max()),
    )


def load_manifest(path: Path = MANIFEST, root_dir: Path = CLEAN_ROOT) -> list[ExportEntry]:
    if not path.exists():
        raise FileNotFoundError(path)
    entries: list[ExportEntry] = []
    for row in csv.DictReader(path.open(encoding="utf-8")):
        root = root_dir / "exports" / row["folder"]
        pine_names = [p for p in (row.get("pine_files") or "").split("|") if p]
        if not pine_names:
            raise ValueError(f"{row['folder']}: no pine_files in manifest")
        pine_name = "source.pine" if "source.pine" in pine_names else pine_names[0]
        pine_path = root / pine_name
        if not pine_path.exists():
            raise FileNotFoundError(pine_path)
        charts: list[ChartExport] = []
        for chart_name in [p for p in (row.get("chart_csv_files") or "").split("|") if p]:
            chart_path = root / chart_name
            charts.append(build_chart_export(chart_path))
        if not charts:
            raise ValueError(f"{row['folder']}: no chart CSVs")
        entries.append(
            ExportEntry(
                export_id=int(row["id"]),
                folder=row["folder"],
                kind=row["kind"],
                source_group=row["source_group"],
                root=root,
                pine_path=pine_path,
                charts=tuple(sorted(charts, key=lambda c: c.timeframe)),
            )
        )
    return entries


def filter_entries(
    entries: list[ExportEntry],
    *,
    kind: str,
    timeframe: str | None,
    limit: int | None,
    start_id: int | None,
    only_id: set[int] | None,
) -> list[ExportEntry]:
    out: list[ExportEntry] = []
    tf = normalize_tf(timeframe) if timeframe else None
    for entry in entries:
        if kind != "all" and entry.kind != kind:
            continue
        if start_id is not None and entry.export_id < start_id:
            continue
        if only_id and entry.export_id not in only_id:
            continue
        if tf and tf not in {chart.timeframe for chart in entry.charts}:
            continue
        out.append(entry)
        if limit is not None and len(out) >= limit:
            break
    return out


def load_source_registry():
    from openpine.pine.registry import SQLitePineSourceRegistry

    return SQLitePineSourceRegistry()


def load_strategy_registry():
    from openpine.registry import SQLiteStrategyRegistry

    return SQLiteStrategyRegistry()


def get_or_add_source(entry: ExportEntry, *, write: bool) -> tuple[Any | None, bool]:
    name = openpine_name(entry)
    source_text = entry.pine_path.read_text(encoding="utf-8")
    if not write:
        return None, False
    registry = load_source_registry()
    try:
        try:
            return registry.get_source(name), False
        except KeyError:
            source = registry.add_source(source_text, name)
            registry._conn.execute(
                "UPDATE pine_sources SET source_type = ?, source_path = ? WHERE id = ?",
                (entry.kind, str(entry.pine_path), source.id),
            )
            registry._conn.commit()
            source.source_type = entry.kind
            source.source_path = str(entry.pine_path)
            return source, True
    finally:
        registry.close()


def compile_source(source: Any, *, force: bool) -> tuple[str | None, dict[str, Any]]:
    from openpine.compile import SubprocessCompilerAdapter, compile_pipeline
    from openpine.pine.registry import SQLitePineSourceRegistry

    if source.active_artifact_id and not force:
        return source.active_artifact_id, {"status": "cached", "artifact_id": source.active_artifact_id}
    result = compile_pipeline(source, SubprocessCompilerAdapter())
    if result.get("success"):
        registry = SQLitePineSourceRegistry()
        try:
            registry.set_active_artifact(source.id, result["artifact_id"])
        finally:
            registry.close()
        return result["artifact_id"], {"status": "compiled", **result}
    return None, {"status": "compile_error", **result}


def timed_call(timings: dict[str, float], key: str, fn: Any, *args: Any, **kwargs: Any) -> Any:
    start = time.perf_counter()
    try:
        return fn(*args, **kwargs)
    finally:
        timings[key] = round(time.perf_counter() - start, 3)


def ensure_strategy_instance(entry: ExportEntry, source: Any, artifact_id: str, timeframe: str) -> tuple[str, bool]:
    registry = load_strategy_registry()
    name = strategy_name(entry, timeframe)
    try:
        for existing in registry.list_strategies():
            if existing.name == name:
                return existing.strategy_id, False
        created = registry.register_strategy(
            artifact_id=artifact_id,
            symbol="BTCUSDT",
            timeframe=timeframe,
            params={},
            name=name,
            pine_id=source.id,
            exchange="binance",
            market_type="spot",
            price_type="trade",
        )
        registry.update_status(created.strategy_id, "pending")
        return created.strategy_id, True
    finally:
        registry.close()


def build_progress_callback(label: str, progress_every: int) -> Any | None:
    progress_every = int(progress_every or 0)
    if progress_every <= 0:
        return None
    last_progress = 0

    def _progress(done: int, total: int) -> None:
        nonlocal last_progress
        if done == total or done - last_progress >= progress_every:
            last_progress = done
            print(f"    runtime {label}: {done}/{total} bars", flush=True)

    return _progress


def load_calculation_bars(
    chart: ChartExport,
    args: argparse.Namespace,
    timings: dict[str, float],
) -> tuple[list[Any], dict[str, Any]]:
    """Load full calculation/prehistory bars through OpenPine's data boundary."""
    from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe
    from openpine.data.orchestrator import DataOrchestrator
    from openpine.data.provider_adapter import create_local_marketdata_provider_adapter
    from openpine.exports import parse_time_ms

    calculation_from = parse_time_ms(args.calculation_from)
    if calculation_from is None:
        raise ValueError("--calculation-from is required for run phase")
    calculation_to_by_tf = getattr(args, "_calculation_to_by_timeframe", {})
    calculation_to = parse_time_ms(args.calculation_to) or calculation_to_by_tf.get(chart.timeframe) or chart.end_ms
    if calculation_from >= calculation_to:
        raise ValueError(f"invalid calculation window: {calculation_from} >= {calculation_to}")
    cache_key = (
        args.symbol.upper(),
        args.exchange.upper(),
        args.market_type.lower(),
        chart.timeframe,
        calculation_from,
        calculation_to,
    )

    if cache_key in BAR_CACHE:
        bars = BAR_CACHE[cache_key]
        timings["data_load_sec"] = 0.0
        cache_hit = True
        data_fetch_info = {"cache": "process", "cache_hit": True}
    else:
        cache_hit = False
        data_fetch_info = None

    if not cache_hit:
        query = BarQuery(
            instrument=InstrumentKey(
                exchange=args.exchange,
                market=args.market_type,
                symbol=args.symbol,
            ),
            timeframe=parse_timeframe(chart.timeframe),
            start_ms=calculation_from,
            end_ms=calculation_to,
        )

        t0 = time.perf_counter()
        provider = create_local_marketdata_provider_adapter()
        orchestrator = DataOrchestrator(provider=provider)
        bars = list(orchestrator.load_bars(query).bars)
        timings["data_load_sec"] = round(time.perf_counter() - t0, 3)
        BAR_CACHE[cache_key] = bars
        data_fetch_info = getattr(getattr(provider, "_provider", None), "last_fetch_info", None)
    if not bars:
        raise RuntimeError(
            f"no calculation bars from OpenPine DataOrchestrator for {args.symbol} {chart.timeframe} "
            f"{ms_to_utc_iso(calculation_from)}..{ms_to_utc_iso(calculation_to)}"
        )

    return bars, {
        "symbol": args.symbol,
        "exchange": args.exchange,
        "market_type": args.market_type,
        "timeframe": chart.timeframe,
        "calculation_from": calculation_from,
        "calculation_to": calculation_to,
        "calculation_from_iso": ms_to_utc_iso(calculation_from),
        "calculation_to_iso": ms_to_utc_iso(calculation_to),
        "compare_from": chart.start_ms,
        "compare_to": chart.end_ms,
        "compare_from_iso": ms_to_utc_iso(chart.start_ms),
        "compare_to_iso": ms_to_utc_iso(chart.end_ms),
        "bars_total": len(bars),
        "visible_bars": chart.bars,
        "cache_hit": cache_hit,
        "data_fetch": data_fetch_info,
    }


def export_object_records(records: Any, path: Path) -> int:
    if records is None:
        pd.DataFrame().to_csv(path, index=False)
        return 0
    rows = []
    for item in list(records or []):
        if hasattr(item, "__dict__"):
            rows.append(dict(item.__dict__))
        elif isinstance(item, dict):
            rows.append(item)
        else:
            rows.append({"value": repr(item)})
    pd.DataFrame(rows).to_csv(path, index=False)
    return len(rows)


def run_indicator(
    entry: ExportEntry,
    source: Any,
    artifact_id: str,
    chart: ChartExport,
    out_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    from backtest_engine.execution_backends.pine_runtime import PineRuntimeBackend
    from openpine.exports import export_plot_records
    from openpine.runtime.engine import load_generated_class_from_artifact

    timings: dict[str, float] = {}
    bars, data_meta = load_calculation_bars(chart, args, timings)
    compare_from, compare_to = chart.start_ms, chart.end_ms
    generated_class = timed_call(
        timings,
        "load_artifact_sec",
        load_generated_class_from_artifact,
        source.id,
        artifact_id,
    )
    config = SimpleNamespace(
        symbol=args.symbol,
        timeframe=chart.timeframe,
        parity_mode=None,
        process_orders_on_close=None,
        calc_on_order_fills=None,
        calc_on_every_tick=None,
        mintick=0.01,
        currency="USD",
        data_provider=None,
        exchange=args.exchange.lower(),
        market_type=args.market_type.lower(),
    )
    progress = build_progress_callback(f"{entry.export_id:04d}/{chart.timeframe}", args.progress_every)
    t0 = time.perf_counter()
    backend_result = PineRuntimeBackend().execute(
        generated_class,
        bars,
        config=config,
        execution_window=None,
        runtime_kwargs={
            "symbol": args.symbol,
            "timeframe": chart.timeframe,
            "data_provider": None,
            "plot_from_ms": compare_from,
            "plot_to_ms": compare_to,
            "progress_callback": progress,
        },
        params={},
        is_indicator=True,
    )
    timings["runtime_sec"] = round(time.perf_counter() - t0, 3)
    plots_csv = out_dir / "plots.csv"
    t0 = time.perf_counter()
    plot_rows = export_plot_records(
        list(getattr(backend_result, "plots", []) or []),
        plots_csv,
        from_ms=compare_from,
        to_ms=compare_to,
    )
    timings["export_sec"] = round(time.perf_counter() - t0, 3)
    return {
        "status": "ok",
        "kind": "indicator",
        "export_format": "openpine_normalized_tv_like_v1",
        "bars": len(bars),
        "data": data_meta,
        "plots_rows": plot_rows,
        "timings": timings,
        "outputs": {"plots": str(plots_csv)},
    }


def run_strategy(
    entry: ExportEntry,
    source: Any,
    artifact_id: str,
    chart: ChartExport,
    out_dir: Path,
    args: argparse.Namespace,
) -> dict[str, Any]:
    from backtest_engine.execution_backends.pine_runtime import PineRuntimeBackend
    from openpine.artifacts import ArtifactStore
    from openpine.exports import ExportWindow, export_equity_curve, export_plot_records, export_trades
    from openpine.runtime.engine import BacktestEngineAdapter, BacktestRunConfig, load_strategy_class_from_artifact

    timings: dict[str, float] = {}
    bars, data_meta = load_calculation_bars(chart, args, timings)
    compare_from, compare_to = chart.start_ms, chart.end_ms
    strategy_class = timed_call(
        timings,
        "load_artifact_sec",
        load_strategy_class_from_artifact,
        source.id,
        artifact_id,
        symbol=args.symbol,
        timeframe=chart.timeframe,
    )
    artifact = timed_call(timings, "load_compile_meta_sec", ArtifactStore().get_artifact, artifact_id, source.id)
    decl = artifact.get("compile_meta", {}).get("translation_metadata", {}).get("declaration", {})
    decl_args = decl.get("arguments", {})
    config = BacktestRunConfig(
        symbol=args.symbol,
        timeframe=chart.timeframe,
        exchange=args.exchange,
        market_type=args.market_type,
        start_time=data_meta["calculation_from"],
        end_time=data_meta["calculation_to"],
        initial_capital=decl_args.get("initial_capital", 10_000.0),
        default_qty_type=decl_args.get("default_qty_type", "fixed"),
        default_qty_value=decl_args.get("default_qty_value", 1.0),
        commission_type=decl_args.get("commission_type", "none"),
        commission_value=decl_args.get("commission_value", 0.0),
        exit_matching=decl_args.get("close_entries_rule", "fifo").upper(),
        pyramiding=decl_args.get("pyramiding", 0),
        qty_step=args.qty_step,
        qty_rounding_mode=args.qty_rounding_mode,
        plot_from_ms=compare_from,
        plot_to_ms=compare_to,
    )
    backend = None
    runtime_class = strategy_class
    if hasattr(strategy_class, "generated_strategy_class_ref"):
        runtime_class = strategy_class.generated_strategy_class_ref
        backend = PineRuntimeBackend()
    progress = build_progress_callback(f"{entry.export_id:04d}/{chart.timeframe}", args.progress_every)
    t0 = time.perf_counter()
    result = BacktestEngineAdapter().run(
        runtime_class,
        bars,
        config,
        params={},
        execution_backend=backend,
        progress_callback=progress,
    )
    timings["runtime_sec"] = round(time.perf_counter() - t0, 3)
    raw = result.raw_result
    plots = getattr(raw, "plots", None)
    t0 = time.perf_counter()
    if plots is None:
        plot_rows = 0
    elif isinstance(plots, list):
        plot_rows = export_plot_records(plots, out_dir / "plots.csv", from_ms=compare_from, to_ms=compare_to)
    elif hasattr(plots, "get_records"):
        plot_rows = export_plot_records(plots.get_records(), out_dir / "plots.csv", from_ms=compare_from, to_ms=compare_to)
    else:
        plot_rows = 0
    export_window = ExportWindow(compare_from, compare_to)
    trades_rows = export_trades(
        list(getattr(raw, "trades", []) or []),
        out_dir / "trades.csv",
        window=export_window,
    )
    equity_rows = export_equity_curve(
        getattr(raw, "equity_curve", None),
        out_dir / "equity_curve.csv",
        window=export_window,
    )
    timings["export_sec"] = round(time.perf_counter() - t0, 3)
    return {
        "status": "ok",
        "kind": "strategy",
        "export_format": "openpine_normalized_tv_like_v1",
        "bars": result.bars_processed,
        "data": data_meta,
        "engine_status": result.status,
        "trades_rows": trades_rows,
        "equity_rows": equity_rows,
        "plots_rows": plot_rows,
        "timings": timings,
        "outputs": {
            "plots": str(out_dir / "plots.csv"),
            "trades": str(out_dir / "trades.csv"),
            "equity_curve": str(out_dir / "equity_curve.csv"),
        },
    }


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")


def append_jsonl(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(payload, ensure_ascii=False, default=str) + "\n")


def result_has_error(result: dict[str, Any]) -> bool:
    if result.get("status") in {"compile_error", "fatal_error", "partial_or_error", "run_error"}:
        return True
    return any((run.get("status") != "ok") for run in result.get("runs", []))


def _expected_output_files(entry: ExportEntry, chart: ChartExport) -> list[Path]:
    """Return the list of output files expected for one chart run.

    Per TZ §P0 batch correctness: --skip-completed must validate that expected
    output files actually exist, not only that the status JSON says "ok".
    """
    out_dir = entry.root / "openpine_outputs" / chart.timeframe
    if entry.kind == "indicator":
        return [out_dir / "plots.csv"]
    # strategy: must have all three canonical output files
    return [out_dir / "plots.csv", out_dir / "trades.csv", out_dir / "equity_curve.csv"]


def completed_for_selection(entry: ExportEntry, args: argparse.Namespace) -> bool:
    if not args.skip_completed:
        return False
    status_path = entry.root / "openpine_outputs" / "openpine_batch_status.json"
    if not status_path.exists():
        return False
    try:
        status = json.loads(status_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False
    if status.get("phase") != args.phase:
        return False
    if args.phase == "run":
        # Per TZ: validate output files AND run_meta.json, not just status JSON
        run_meta_ok = all(
            (entry.root / "openpine_outputs" / chart.timeframe / "run_meta.json").exists()
            for chart in entry.charts
            if not args.timeframe or chart.timeframe == normalize_tf(args.timeframe)
        )
        if not run_meta_ok:
            return False
        # Check status + output file existence for each chart
        wanted_charts = [
            c for c in entry.charts
            if not args.timeframe or c.timeframe == normalize_tf(args.timeframe)
        ]
        for chart in wanted_charts:
            for expected in _expected_output_files(entry, chart):
                if not expected.exists():
                    return False
        wanted_tfs = {c.timeframe for c in wanted_charts}
        ok_runs = {run.get("timeframe") for run in status.get("runs", []) if run.get("status") == "ok"}
        return status.get("status") == "ok" and wanted_tfs <= ok_runs
    return status.get("status") in {"planned", "ingested", "compiled", "registered", "ok"}


def entry_summary(entry: ExportEntry) -> dict[str, Any]:
    return {
        "id": entry.export_id,
        "folder": entry.folder,
        "kind": entry.kind,
        "source_group": entry.source_group,
        "pine": str(entry.pine_path),
        "charts": [
            {
                "timeframe": c.timeframe,
                "path": str(c.path),
                "bars": c.bars,
                "start_ms": c.start_ms,
                "end_ms": c.end_ms,
            }
            for c in entry.charts
        ],
        "openpine_name": openpine_name(entry),
    }


def run_entry(entry: ExportEntry, args: argparse.Namespace, batch_id: str = "", library_revisions: dict[str, str] | None = None) -> dict[str, Any]:
    started = time.perf_counter()
    timings: dict[str, float] = {}
    status: dict[str, Any] = {
        **entry_summary(entry),
        "phase": args.phase,
        "started_at": utc_now(),
        "status": "planned",
        "timings": timings,
        "batch_id": batch_id,
        "library_revisions": library_revisions or {},
    }

    if args.phase == "plan":
        status["elapsed_sec"] = round(time.perf_counter() - started, 3)
        return status

    source, added = timed_call(timings, "ingest_sec", get_or_add_source, entry, write=True)
    status["source_id"] = source.id
    status["source_added"] = added
    status["status"] = "ingested"
    if args.phase == "ingest":
        status["elapsed_sec"] = round(time.perf_counter() - started, 3)
        return status

    artifact_id, compile_info = timed_call(timings, "compile_sec", compile_source, source, force=args.force_compile)
    status["compile"] = {
        "status": compile_info.get("status"),
        "artifact_id": artifact_id,
        "errors": compile_info.get("errors", []),
    }
    if artifact_id is None:
        status["status"] = "compile_error"
        status["elapsed_sec"] = round(time.perf_counter() - started, 3)
        return status
    status["artifact_id"] = artifact_id
    status["status"] = "compiled"
    if args.phase == "compile":
        status["elapsed_sec"] = round(time.perf_counter() - started, 3)
        return status

    t0 = time.perf_counter()
    registered: list[dict[str, Any]] = []
    if entry.kind == "strategy":
        for chart in entry.charts:
            if args.timeframe and chart.timeframe != normalize_tf(args.timeframe):
                continue
            sid, created = ensure_strategy_instance(entry, source, artifact_id, chart.timeframe)
            registered.append({"timeframe": chart.timeframe, "strategy_id": sid, "created": created})
    timings["register_sec"] = round(time.perf_counter() - t0, 3)
    status["registered_strategies"] = registered
    status["status"] = "registered"
    if args.phase == "register":
        status["elapsed_sec"] = round(time.perf_counter() - started, 3)
        return status

    t0 = time.perf_counter()
    runs: list[dict[str, Any]] = []
    for chart in entry.charts:
        if args.timeframe and chart.timeframe != normalize_tf(args.timeframe):
            continue
        out_dir = entry.root / "openpine_outputs" / chart.timeframe
        out_dir.mkdir(parents=True, exist_ok=True)
        try:
            if entry.kind == "indicator":
                run_info = run_indicator(entry, source, artifact_id, chart, out_dir, args)
            elif entry.kind == "strategy":
                run_info = run_strategy(entry, source, artifact_id, chart, out_dir, args)
            else:
                run_info = {"status": "skipped", "reason": f"unsupported kind {entry.kind}"}
        except Exception as exc:
            run_info = {
                "status": "run_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=8),
            }
        run_info["timeframe"] = chart.timeframe
        run_info["output_dir"] = str(out_dir)
        # batch_id and library_revisions already in status dict
        write_json(out_dir / "run_meta.json", {**status, "run": run_info})
        runs.append(run_info)
    timings["run_sec"] = round(time.perf_counter() - t0, 3)
    status["runs"] = runs
    status["status"] = "ok" if all(r.get("status") == "ok" for r in runs) else "partial_or_error"
    status["elapsed_sec"] = round(time.perf_counter() - started, 3)
    return status


def parse_ids(value: str | None) -> set[int] | None:
    if not value:
        return None
    ids: set[int] = set()
    for part in value.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            a, b = part.split("-", 1)
            ids.update(range(int(a), int(b) + 1))
        else:
            ids.add(int(part))
    return ids


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    stats: dict[str, int] = {}
    by_kind: dict[str, int] = {}
    by_tf: dict[str, int] = {}
    for r in results:
        stats[r.get("status", "unknown")] = stats.get(r.get("status", "unknown"), 0) + 1
        by_kind[r.get("kind", "unknown")] = by_kind.get(r.get("kind", "unknown"), 0) + 1
        for chart in r.get("charts", []):
            tf = chart.get("timeframe", "unknown")
            by_tf[tf] = by_tf.get(tf, 0) + 1
    return {"stats": stats, "by_kind": by_kind, "by_timeframe": by_tf}


def resolve_calculation_to_by_timeframe(
    entries: list[ExportEntry],
    args: argparse.Namespace,
) -> dict[str, int]:
    """Use one calculation end per timeframe so a batch can reuse prehistory bars."""

    if args.phase != "run" or args.calculation_to:
        return {}
    out: dict[str, int] = {}
    for entry in entries:
        for chart in entry.charts:
            if args.timeframe and chart.timeframe != normalize_tf(args.timeframe):
                continue
            out[chart.timeframe] = max(out.get(chart.timeframe, 0), chart.end_ms)
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=CLEAN_ROOT)
    parser.add_argument("--manifest", type=Path, default=MANIFEST)
    parser.add_argument(
        "--phase",
        choices=["plan", "ingest", "compile", "register", "run"],
        default="plan",
        help="Execution phase. plan is safe and writes no DB state.",
    )
    parser.add_argument("--kind", choices=["all", "indicator", "strategy"], default="all")
    parser.add_argument("--timeframe", default=None, help="Optional timeframe filter: 15m, 1D, 1h")
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-id", type=int, default=None)
    parser.add_argument("--ids", default=None, help="Comma/range list, e.g. 1,2,1501-1528")
    parser.add_argument("--force-compile", action="store_true")
    parser.add_argument("--stop-on-error", action="store_true")
    parser.add_argument("--summary-name", default=None)
    parser.add_argument("--errors-name", default="openpine_batch_errors.jsonl")
    parser.add_argument("--skip-completed", action="store_true", help="Skip entries already completed for the selected phase.")
    parser.add_argument("--symbol", default="BTCUSDT")
    parser.add_argument("--exchange", default="binance")
    parser.add_argument("--market-type", default="spot")
    parser.add_argument(
        "--calculation-from",
        default="2017-01-01",
        help="Full calculation/prehistory start. Visible export window still comes from TV chart CSV.",
    )
    parser.add_argument("--calculation-to", default=None, help="Optional calculation end. Default: TV visible window end.")
    parser.add_argument("--progress-every", type=int, default=10_000, help="Runtime bar progress interval. 0 disables.")
    parser.add_argument("--qty-step", type=float, default=1e-6)
    parser.add_argument("--qty-rounding-mode", default="truncate")
    args = parser.parse_args(argv)

    entries = load_manifest(args.manifest, args.root)
    selected = filter_entries(
        entries,
        kind=args.kind,
        timeframe=args.timeframe,
        limit=args.limit,
        start_id=args.start_id,
        only_id=parse_ids(args.ids),
    )
    args._calculation_to_by_timeframe = resolve_calculation_to_by_timeframe(selected, args)
    batch_id = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    summary_path = args.root / (args.summary_name or f"openpine_batch_{args.phase}_{batch_id}.json")
    errors_path = args.root / args.errors_name
    library_revisions = _get_library_revisions()

    print(f"root={args.root}")
    print(f"manifest={args.manifest}")
    print(f"phase={args.phase} selected={len(selected)} total={len(entries)}")
    if args._calculation_to_by_timeframe:
        resolved = {
            tf: ms_to_utc_iso(value)
            for tf, value in sorted(args._calculation_to_by_timeframe.items())
        }
        print(f"calculation_to_by_timeframe={json.dumps(resolved, ensure_ascii=False)}")

    _write_progress(args.root, batch_id, None, args.phase, "running", f"selected={len(selected)}")
    results: list[dict[str, Any]] = []
    for idx, entry in enumerate(selected, 1):
        _write_progress(args.root, batch_id, entry.export_id, args.phase, "entry_start", entry.folder)
        print(f"[{idx}/{len(selected)}] {entry.export_id:04d} {entry.kind} {entry.folder}")
        if completed_for_selection(entry, args):
            result = {**entry_summary(entry), "phase": args.phase, "status": "skipped_completed"}
            results.append(result)
            print("  -> skipped_completed")
            _write_progress(args.root, batch_id, entry.export_id, args.phase, "skipped_completed")
            continue
        try:
            result = run_entry(entry, args, batch_id=batch_id, library_revisions=library_revisions)
        except Exception as exc:
            result = {
                **entry_summary(entry),
                "phase": args.phase,
                "status": "fatal_error",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "traceback": traceback.format_exc(limit=10),
            }
            if args.stop_on_error:
                results.append(result)
                append_jsonl(errors_path, {**result, "created_at": utc_now()})
                break
        result["batch_id"] = batch_id
        results.append(result)
        status_record = {**result, "batch_id": batch_id, "library_revisions": library_revisions}
        write_json(entry.root / "openpine_outputs" / "openpine_batch_status.json", status_record)
        if result_has_error(result):
            append_jsonl(errors_path, {**result, "created_at": utc_now()})
        _write_progress(args.root, batch_id, entry.export_id, args.phase, result.get("status", "unknown"))
        print(f"  -> {result.get('status')}")
        if args.stop_on_error and result.get("status") in {"compile_error", "fatal_error", "partial_or_error"}:
            break

    # Write durable current_progress.json marking batch complete
    final_status = "completed" if all(r.get("status") != "fatal_error" for r in results) else "failed"
    _write_progress(args.root, batch_id, None, args.phase, final_status)

    # Per-timeframe summary CSV
    tf_rows: list[dict[str, Any]] = []
    for r in results:
        for run in r.get("runs", []):
            tf_rows.append({
                "batch_id": batch_id,
                "export_id": r.get("id"),
                "kind": r.get("kind"),
                "timeframe": run.get("timeframe"),
                "status": run.get("status"),
                "bars": run.get("bars"),
                "plots_rows": run.get("plots_rows"),
                "trades_rows": run.get("trades_rows"),
                "equity_rows": run.get("equity_rows"),
            })
    if tf_rows:
        tf_df = pd.DataFrame(tf_rows)
        tf_summary_path = args.root / f"openpine_batch_{args.phase}_{batch_id}_by_timeframe.csv"
        tf_df.to_csv(tf_summary_path, index=False)
        print(f"per-timeframe summary={tf_summary_path}")

    payload = {
        "batch_id": batch_id,
        "created_at": utc_now(),
        "phase": args.phase,
        "root": str(args.root),
        "manifest": str(args.manifest),
        "errors": str(errors_path),
        "symbol": args.symbol,
        "exchange": args.exchange,
        "market_type": args.market_type,
        "calculation_from": args.calculation_from,
        "calculation_to": args.calculation_to,
        "calculation_to_by_timeframe": {
            tf: ms_to_utc_iso(value)
            for tf, value in sorted(getattr(args, "_calculation_to_by_timeframe", {}).items())
        },
        "bar_cache_entries": len(BAR_CACHE),
        "selected": len(selected),
        "total_manifest_entries": len(entries),
        "library_revisions": library_revisions,
        "summary": summarize(results),
        "results": results,
    }
    write_json(summary_path, payload)
    print(f"summary={summary_path}")
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
