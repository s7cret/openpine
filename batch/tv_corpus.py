"""Typed TradingView corpus boundary for OpenPine batch runs."""

from __future__ import annotations

import csv
import os
import re
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

DEFAULT_WORKSPACE = Path(os.environ.get("OPENPINE_WORKSPACE_ROOT", Path.cwd()))
CLEAN_ROOT = DEFAULT_WORKSPACE / "pine_oracle_1528_tv_exports_clean_20260529"
MANIFEST = CLEAN_ROOT / "manifest.csv"


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
    with path.open(encoding="utf-8", newline="") as fh:
        for row in csv.DictReader(fh):
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
