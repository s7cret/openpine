"""Persistent marketdata cache shared by OpenPine CLI and batch runs."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
from typing import Any

import pandas as pd
from openpine.data.cache_io import read_valid_meta, write_csv_atomic, write_json_atomic
from marketdata_provider.contracts import Bar, BarQuery, BarSeries

CACHE_SCHEMA_VERSION = 1
CACHE_PROGRESS_CHUNK_ROWS = 1000


def default_cache_dir() -> Path:
    return Path(
        os.environ.get(
            "OPENPINE_DATA_CACHE_DIR", Path.home() / ".cache" / "openpine" / "data"
        )
    )


def cache_enabled_by_env() -> bool:
    return os.environ.get("OPENPINE_DATA_CACHE", "1").lower() not in {
        "0",
        "false",
        "no",
        "off",
    }


def query_key(query: BarQuery) -> dict[str, Any]:
    return {
        "schema": CACHE_SCHEMA_VERSION,
        "instrument": {
            "exchange": query.instrument.exchange.lower(),
            "market": query.instrument.market.lower(),
            "symbol": query.instrument.symbol.upper(),
        },
        "timeframe": query.timeframe.canonical,
        "start_ms": int(query.start_ms),
        "end_ms": int(query.end_ms),
        "source": query.source,
        "gap_policy": query.gap_policy,
    }


def load_bar_series(
    cache_dir: Path, query: BarQuery, progress_callback=None
) -> BarSeries | None:
    key_payload = query_key(query)
    key = _cache_key(key_payload)
    meta_path, data_path = _paths(cache_dir, key)
    meta = read_valid_meta(meta_path, key_payload, schema_version=CACHE_SCHEMA_VERSION)
    if meta is None or not data_path.exists():
        return None

    total_rows = _safe_int(meta.get("rows"), fallback=0)
    _emit_cache_progress(progress_callback, 0, total_rows, "cache_read")
    bars_list: list[Bar] = []
    for df in pd.read_csv(data_path, chunksize=CACHE_PROGRESS_CHUNK_ROWS):
        bars_list.extend(_bars_from_dataframe(query, df))
        _emit_cache_progress(
            progress_callback,
            len(bars_list),
            total_rows or len(bars_list),
            "cache_read",
        )
    bars = tuple(bars_list)
    _emit_cache_progress(
        progress_callback,
        len(bars),
        total_rows or len(bars),
        "cache_hit",
    )
    coverage = _coverage_for(query, bars, source="persistent_cache")
    return BarSeries(query=query, bars=bars, coverage=coverage)


def _safe_int(value: Any, *, fallback: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _emit_cache_progress(progress_callback, done: int, total: int, phase: str) -> None:
    if progress_callback is None:
        return
    total = max(0, int(total or 0))
    total_pages = max(1, math.ceil(total / CACHE_PROGRESS_CHUNK_ROWS)) if total else 0
    pages = min(total_pages, math.ceil(done / CACHE_PROGRESS_CHUNK_ROWS)) if done else 0
    try:
        progress_callback(done, pages, total, total_pages, None, phase)
    except Exception:
        return


def _bars_from_dataframe(query: BarQuery, df: pd.DataFrame) -> list[Bar]:
    return [
        Bar(
            instrument=query.instrument,
            timeframe=query.timeframe,
            time=int(row.time),
            time_close=int(row.time_close),
            open=float(row.open),
            high=float(row.high),
            low=float(row.low),
            close=float(row.close),
            volume=None if pd.isna(row.volume) else float(row.volume),
            closed=bool(row.closed),
        )
        for row in df.itertuples(index=False)
    ]


def save_bar_series(cache_dir: Path, series: BarSeries) -> None:
    key_payload = query_key(series.query)
    key = _cache_key(key_payload)
    meta_path, data_path = _paths(cache_dir, key)
    rows = [
        {
            "time": int(bar.time),
            "time_close": int(bar.time_close),
            "open": float(bar.open),
            "high": float(bar.high),
            "low": float(bar.low),
            "close": float(bar.close),
            "volume": getattr(bar, "volume", None),
            "closed": bool(getattr(bar, "closed", True)),
        }
        for bar in series.bars
    ]
    write_csv_atomic(pd.DataFrame(rows), data_path)
    write_json_atomic(
        {
            "schema_version": CACHE_SCHEMA_VERSION,
            "key": key_payload,
            "rows": len(rows),
            "first_time": rows[0]["time"] if rows else None,
            "last_time": rows[-1]["time"] if rows else None,
        },
        meta_path,
    )


def _cache_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _paths(cache_dir: Path, key: str) -> tuple[Path, Path]:
    return cache_dir / f"{key}.json", cache_dir / f"{key}.csv"




def _coverage_for(query: BarQuery, bars: tuple[Bar, ...], source: str):
    from openpine.data.orchestrator import DataOrchestrator

    return DataOrchestrator.coverage_for_series(query, bars, source)
