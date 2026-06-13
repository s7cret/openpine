"""Optional Parquet compatibility helpers.

OpenPine writes production artifacts through pyarrow when it is installed.  The
backend package must also remain importable in lightweight/offline test and CI
environments where pyarrow wheels are intentionally absent.  This module keeps
that boundary explicit: callers use dataframe-level helpers and receive the same
logical data back, while the fallback stores a pandas pickle at the requested
path.  The fallback is only for hermetic/local gates; production deployments keep
using pyarrow.
"""

from __future__ import annotations

from dataclasses import dataclass
import os
from pathlib import Path
from typing import Iterable

import pandas as pd

try:  # pragma: no cover - exercised when production pyarrow is installed
    import pyarrow as _pa
    import pyarrow.parquet as _pq
except Exception:  # pragma: no cover - fallback itself is covered instead
    _pa = None
    _pq = None


@dataclass(frozen=True)
class ParquetField:
    name: str
    dtype: str
    nullable: bool = False


@dataclass(frozen=True)
class ParquetSchema:
    fields: tuple[ParquetField, ...]

    def __str__(self) -> str:
        return "\n".join(
            f"{field.name}: {field.dtype}{' nullable' if field.nullable else ''}"
            for field in self.fields
        )


def schema(fields: Iterable[tuple[str, str] | tuple[str, str, bool]]) -> object:
    """Build a pyarrow schema when available, otherwise a stable schema object."""

    normalized = tuple(
        ParquetField(
            name=str(item[0]),
            dtype=str(item[1]),
            nullable=bool(item[2]) if len(item) > 2 else False,
        )
        for item in fields
    )
    if _pa is None:
        return ParquetSchema(normalized)

    arrow_fields = []
    for field in normalized:  # pragma: no cover - depends on pyarrow
        arrow_type = {
            "string": _pa.string(),
            "int64": _pa.int64(),
            "float64": _pa.float64(),
            "bool": _pa.bool_(),
        }.get(field.dtype)
        if arrow_type is None:
            raise ValueError(f"Unsupported parquet field type: {field.dtype}")
        arrow_fields.append(_pa.field(field.name, arrow_type, nullable=field.nullable))
    return _pa.schema(arrow_fields)


def pyarrow_available() -> bool:
    return _pa is not None and _pq is not None


def write_dataframe(
    df: pd.DataFrame,
    path: str | Path,
    *,
    schema: object | None = None,
    compression: str = "zstd",
) -> None:
    """Write dataframe to a parquet-compatible artifact path."""

    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    if pyarrow_available():  # pragma: no cover - depends on pyarrow
        table = _pa.Table.from_pandas(df, schema=schema, preserve_index=False)
        _pq.write_table(table, str(output), compression=compression)
        return
    df.to_json(output, orient="table", index=False)


def _require_parquet_backend():
    if _pq is None:
        raise RuntimeError("pyarrow parquet backend is unavailable")
    return _pq


def _read_fallback_dataframe(source: Path) -> pd.DataFrame:
    try:
        loaded = pd.read_json(source, orient="table")
        if not isinstance(loaded, pd.DataFrame):
            raise ValueError("fallback parquet artifact did not contain a dataframe")
        return loaded
    except (UnicodeDecodeError, ValueError) as exc:
        if os.environ.get("OPENPINE_ALLOW_LEGACY_PICKLE_PARQUET") == "1":
            return pd.read_pickle(source)  # noqa: S301 - explicit trusted legacy opt-in
        raise RuntimeError(
            "legacy pickle parquet fallback loading is disabled; set "
            "OPENPINE_ALLOW_LEGACY_PICKLE_PARQUET=1 only for trusted local artifacts"
        ) from exc


def read_dataframe(path: str | Path) -> pd.DataFrame:
    """Read a dataframe written by :func:`write_dataframe`."""

    source = Path(path)
    if pyarrow_available():  # pragma: no cover - depends on pyarrow
        pq = _require_parquet_backend()
        return pq.ParquetFile(str(source)).read().to_pandas()
    return _read_fallback_dataframe(source)


def row_count(path: str | Path) -> int:
    """Return the number of rows in a parquet-compatible artifact."""

    if pyarrow_available():  # pragma: no cover - depends on pyarrow
        pq = _require_parquet_backend()
        return pq.ParquetFile(str(path)).metadata.num_rows
    return len(read_dataframe(path))
