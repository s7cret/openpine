"""Equity curve export writer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from openpine.export._utils import first, int_or_none, object_dict
from openpine.export.schemas import EQUITY_COLUMNS
from openpine.export.window import ExportWindow


def export_equity_curve(
    points: Any,
    output_path: str | Path,
    *,
    window: ExportWindow,
) -> int:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for point in list(points or []):
        row = equity_row(point)
        if window.contains(int_or_none(row.get("bar_time_ms"))):
            rows.append(row)
    pd.DataFrame(rows, columns=EQUITY_COLUMNS).to_csv(output, index=False)
    return len(rows)


def initial_equity_at_export_start(
    points: list[Any],
    window: ExportWindow,
) -> Any | None:
    selected: tuple[int, Any] | None = None
    for point in points:
        row = equity_row(point)
        ts_ms = int_or_none(row.get("bar_time_ms"))
        if ts_ms is None or ts_ms > window.from_ms:
            continue
        if selected is None or ts_ms >= selected[0]:
            selected = (ts_ms, row.get("equity"))
    return None if selected is None else selected[1]


def equity_row(point: Any) -> dict[str, Any]:
    data = object_dict(point)
    return {
        "bar_time_ms": first(data, "bar_time_ms", "bar_time", "time", "timestamp"),
        "equity": first(data, "equity"),
        "balance": first(data, "balance", "cash"),
        "open_profit": first(data, "open_profit", "unrealized_pnl"),
        "net_profit": first(data, "net_profit", "net_pnl"),
        "drawdown": first(data, "drawdown"),
        "position_size": first(data, "position_size", "position_qty"),
    }
