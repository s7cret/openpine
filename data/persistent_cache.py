"""Persistent marketdata cache shared by OpenPine CLI and batch runs."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any

import pandas as pd
from marketdata_provider.contracts import Bar, BarQuery, BarSeries

CACHE_SCHEMA_VERSION = 1


def default_cache_dir() -> Path:
    return Path(os.environ.get("OPENPINE_DATA_CACHE_DIR", Path.home() / ".cache" / "openpine" / "data"))


def cache_enabled_by_env() -> bool:
    return os.environ.get("OPENPINE_DATA_CACHE", "1").lower() not in {"0", "false", "no", "off"}


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


def load_bar_series(cache_dir: Path, query: BarQuery) -> BarSeries | None:
    key_payload = query_key(query)
    key = _cache_key(key_payload)
    meta_path, data_path = _paths(cache_dir, key)
    meta = _read_valid_meta(meta_path, key_payload)
    if meta is None or not data_path.exists():
        return None

    df = pd.read_csv(data_path)
    bars = tuple(
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
    )
    coverage = _coverage_for(query, bars, source="persistent_cache")
    return BarSeries(query=query, bars=bars, coverage=coverage)


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
    _write_csv_atomic(pd.DataFrame(rows), data_path)
    _write_json_atomic(
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


def _read_valid_meta(path: Path, key_payload: dict[str, Any]) -> dict[str, Any] | None:
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if meta.get("schema_version") != CACHE_SCHEMA_VERSION:
        return None
    if meta.get("key") != key_payload:
        return None
    return meta


def _coverage_for(query: BarQuery, bars: tuple[Bar, ...], source: str):
    from openpine.data.orchestrator import DataOrchestrator

    return DataOrchestrator.coverage_for_series(query, bars, source)


def _write_json_atomic(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    tmp_path.replace(path)


def _write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp_path, index=False)
    tmp_path.replace(path)
