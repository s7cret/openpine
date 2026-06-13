"""Persistent caches for expensive batch-run data boundaries."""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Any, Iterable

import pandas as pd
from openpine.data.cache_io import read_valid_meta, write_csv_atomic, write_json_atomic

CACHE_SCHEMA_VERSION = 1


def default_cache_dir(root: Path) -> Path:
    return Path(
        os.environ.get("OPENPINE_BATCH_CACHE_DIR", root / ".openpine_batch_cache")
    )


def cache_key(payload: dict[str, Any]) -> str:
    encoded = json.dumps(
        payload, sort_keys=True, separators=(",", ":"), default=str
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def path_fingerprint(paths: Iterable[Path], *, root: Path | None = None) -> str:
    records: list[dict[str, Any]] = []
    for path in sorted(Path(p) for p in paths):
        stat = path.stat()
        display_path = path
        if root is not None:
            try:
                display_path = path.relative_to(root)
            except ValueError:
                pass
        records.append(
            {
                "path": str(display_path),
                "size": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
    return cache_key({"schema": CACHE_SCHEMA_VERSION, "paths": records})


def load_bars(
    cache_dir: Path,
    key_payload: dict[str, Any],
    *,
    instrument: Any,
    timeframe: Any,
) -> tuple[list[Any], dict[str, Any]] | None:
    key = cache_key(key_payload)
    meta_path, data_path = _paths(cache_dir, "bars", key)
    meta = read_valid_meta(meta_path, key_payload, schema_version=CACHE_SCHEMA_VERSION)
    if meta is None or not data_path.exists():
        return None

    from marketdata_provider.contracts import Bar

    df = pd.read_csv(data_path)
    bars = [
        Bar(
            instrument=instrument,
            timeframe=timeframe,
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
    return bars, {**meta, "cache": "persistent", "cache_hit": True}


def save_bars(
    cache_dir: Path, key_payload: dict[str, Any], bars: list[Any]
) -> dict[str, Any]:
    key = cache_key(key_payload)
    meta_path, data_path = _paths(cache_dir, "bars", key)
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
        for bar in bars
    ]
    write_csv_atomic(pd.DataFrame(rows), data_path)
    meta = _build_meta(
        key_payload,
        {
            "rows": len(rows),
            "first_time": rows[0]["time"] if rows else None,
            "last_time": rows[-1]["time"] if rows else None,
        },
    )
    write_json_atomic(meta, meta_path)
    return meta


def load_tv_corpus(
    cache_dir: Path,
    key_payload: dict[str, Any],
) -> tuple[dict[int, dict[str, float]], dict[str, Any]] | None:
    key = cache_key(key_payload)
    meta_path, data_path = _paths(cache_dir, "tv_corpus", key)
    meta = read_valid_meta(meta_path, key_payload, schema_version=CACHE_SCHEMA_VERSION)
    if meta is None or not data_path.exists():
        return None

    df = pd.read_csv(data_path)
    bars = {
        int(row.bar_time): {
            "open": float(row.open),
            "high": float(row.high),
            "low": float(row.low),
            "close": float(row.close),
            "volume": float(row.volume),
        }
        for row in df.itertuples(index=False)
    }
    return bars, {**meta, "cache": "persistent", "cache_hit": True}


def save_tv_corpus(
    cache_dir: Path,
    key_payload: dict[str, Any],
    bars: dict[int, dict[str, float]],
    meta: dict[str, Any],
) -> dict[str, Any]:
    key = cache_key(key_payload)
    meta_path, data_path = _paths(cache_dir, "tv_corpus", key)
    rows = [
        {
            "bar_time": int(bar_time),
            "open": float(payload["open"]),
            "high": float(payload["high"]),
            "low": float(payload["low"]),
            "close": float(payload["close"]),
            "volume": float(payload["volume"]),
        }
        for bar_time, payload in sorted(bars.items())
    ]
    write_csv_atomic(pd.DataFrame(rows), data_path)
    cache_meta = _build_meta(key_payload, {**meta, "unique_bars": len(rows)})
    write_json_atomic(cache_meta, meta_path)
    return cache_meta


def _paths(cache_dir: Path, namespace: str, key: str) -> tuple[Path, Path]:
    base = cache_dir / namespace
    return base / f"{key}.json", base / f"{key}.csv"


def _build_meta(key_payload: dict[str, Any], extra: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": CACHE_SCHEMA_VERSION,
        "key": key_payload,
        **extra,
    }
