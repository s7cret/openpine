"""Canonical Parquet IO. PyArrow is a required dependency, not an optional codec.

A .parquet file must be Parquet regardless of the environment that wrote it.
There is deliberately no JSON/pickle fallback or environment-variable bypass.
"""
from __future__ import annotations

import os
import tempfile
from collections.abc import Iterable, Sequence
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq


def schema(fields: Iterable[tuple[str, str] | tuple[str, str, bool]]) -> pa.Schema:
    """Build an explicit Arrow schema; unsupported field types fail early."""
    types = {"string": pa.string(), "int64": pa.int64(), "float64": pa.float64(),
             "bool": pa.bool_()}
    result = []
    for item in fields:
        if len(item) not in (2, 3):
            raise ValueError("Parquet fields require name, type and optional nullable")
        name, dtype = item[:2]
        if not isinstance(name, str) or not name or dtype not in types:
            raise ValueError(f"Unsupported parquet field: {item!r}")
        nullable = item[2] if len(item) == 3 else False
        if type(nullable) is not bool:
            raise ValueError("Parquet nullable must be a bool")
        result.append(pa.field(name, types[dtype], nullable=nullable))
    if len({field.name for field in result}) != len(result):
        raise ValueError("Duplicate Parquet field name")
    return pa.schema(result)


def write_dataframe(
    df: pd.DataFrame, path: str | Path, *, schema: pa.Schema | None = None,
    compression: str = "zstd",
) -> None:
    """Atomically replace a file only after a real Parquet write succeeds."""
    table = pa.Table.from_pandas(df, schema=schema, preserve_index=False)
    output = Path(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=f".{output.name}.", suffix=".tmp",
                                     dir=output.parent, delete=False) as stream:
        temporary = Path(stream.name)
    try:
        pq.write_table(table, temporary, compression=compression)
        with temporary.open("rb") as stream:
            os.fsync(stream.fileno())
        os.replace(temporary, output)
    finally:
        temporary.unlink(missing_ok=True)


def read_dataframe(path: str | Path, *, columns: Sequence[str] | None = None) -> pd.DataFrame:
    """Read a single file without Hive-partition type inference from its path."""
    with pq.ParquetFile(path) as file:
        return file.read(columns=None if columns is None else list(columns)).to_pandas()


def row_count(path: str | Path) -> int:
    """Read the row count from the footer without materializing a dataframe."""
    with pq.ParquetFile(path) as file:
        return int(file.metadata.num_rows)
