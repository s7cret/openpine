"""Normalized OpenPine run exports for external parity checks."""

from __future__ import annotations

import csv
import json
from dataclasses import asdict, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd


PLOT_COLUMNS = ["bar_time", "bar_index"]

TRADE_COLUMNS = [
    "trade_id",
    "entry_id",
    "exit_id",
    "direction",
    "entry_time",
    "exit_time",
    "entry_price",
    "exit_price",
    "qty",
    "gross_pnl",
    "net_pnl",
    "net_pnl_pct",
    "fee",
    "slippage",
    "bars_held",
    "exit_reason",
]


def _plot_scalar(value: Any) -> Any:
    return getattr(value, "_current", value)


def parse_time_ms(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    if value.isdigit():
        raw = int(value)
        return raw if raw > 10_000_000_000 else raw * 1000
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("UTC")
    return int(timestamp.timestamp() * 1000)


def export_plot_outputs(
    source_path: str | Path,
    output_path: str | Path,
    *,
    from_ms: int | None = None,
    to_ms: int | None = None,
) -> int:
    """Export long plot records as one wide CSV row per chart bar."""
    source = Path(source_path)
    output = Path(output_path)
    if source.suffix == ".parquet":
        df = pd.read_parquet(source)
    else:
        df = pd.read_csv(source)

    if df.empty:
        output.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(columns=PLOT_COLUMNS).to_csv(output, index=False)
        return 0

    if from_ms is not None:
        df = df[df["bar_time"] >= from_ms]
    if to_ms is not None:
        df = df[df["bar_time"] <= to_ms]

    if df.empty:
        wide = pd.DataFrame(columns=PLOT_COLUMNS)
    else:
        wide = (
            df.pivot_table(
                index=PLOT_COLUMNS,
                columns="title",
                values="value",
                aggfunc="last",
                sort=False,
            )
            .reset_index()
            .sort_values(PLOT_COLUMNS)
        )
        wide.columns.name = None
    if "bar_index" in wide.columns:
        wide["bar_index"] = range(len(wide))

    output.parent.mkdir(parents=True, exist_ok=True)
    wide.to_csv(output, index=False)
    return int(len(wide))


def export_plot_records(
    records: list[Any],
    output_path: str | Path,
    *,
    from_ms: int | None = None,
    to_ms: int | None = None,
) -> int:
    """Export in-memory plot records to normalized wide CSV."""
    rows = []
    for rec in records:
        if isinstance(rec, tuple) and len(rec) >= 4:
            rows.append(
                {
                    "bar_time": rec[0],
                    "bar_index": rec[1],
                    "value": _plot_scalar(rec[2]),
                    "title": rec[3],
                }
            )
        elif hasattr(rec, "bar_time"):
            rows.append(
                {
                    "bar_time": rec.bar_time,
                    "bar_index": getattr(rec, "bar_index", None),
                    "value": _plot_scalar(rec.value),
                    "title": rec.title,
                }
            )
    tmp = Path(output_path).with_suffix(".long.tmp.csv")
    pd.DataFrame(rows, columns=["bar_time", "bar_index", "value", "title"]).to_csv(tmp, index=False)
    try:
        return export_plot_outputs(tmp, output_path, from_ms=from_ms, to_ms=to_ms)
    finally:
        tmp.unlink(missing_ok=True)


def export_trades(trades: list[Any], output_path: str | Path) -> int:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=TRADE_COLUMNS)
        writer.writeheader()
        for trade in trades:
            data = _object_dict(trade)
            writer.writerow({column: data.get(column) for column in TRADE_COLUMNS})
    return len(trades)


def write_json(path: str | Path, payload: dict[str, Any]) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def _object_dict(value: Any) -> dict[str, Any]:
    if is_dataclass(value):
        return asdict(value)
    if hasattr(value, "__dict__"):
        return dict(value.__dict__)
    if isinstance(value, dict):
        return value
    return {}
