"""Atomic metadata/dataframe cache IO shared by data cache layers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pandas as pd


def read_valid_meta(
    path: Path, key_payload: dict[str, Any], *, schema_version: int
) -> dict[str, Any] | None:
    try:
        meta = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return None
    if meta.get("schema_version") != schema_version:
        return None
    if meta.get("key") != key_payload:
        return None
    return meta


def write_json_atomic(payload: dict[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    tmp_path.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    tmp_path.replace(path)


def write_csv_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    df.to_csv(tmp_path, index=False)
    tmp_path.replace(path)
