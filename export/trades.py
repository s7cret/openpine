"""Trade export writer."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd

from openpine.export._utils import first, int_or_none, object_dict
from openpine.export.schemas import TRADE_COLUMNS
from openpine.export.window import ExportWindow


def export_trades(
    trades: list[Any],
    output_path: str | Path,
    *,
    window: ExportWindow | None = None,
) -> int:
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = []
    for trade in trades:
        row = trade_row(trade)
        if window is not None:
            status = str(row.get("status") or "").lower()
            if status == "closed":
                if not window.contains(int_or_none(row.get("exit_time_ms"))):
                    continue
            elif not window.contains(int_or_none(row.get("entry_time_ms"))):
                continue
        rows.append(row)
    pd.DataFrame(rows, columns=TRADE_COLUMNS).to_csv(output, index=False)
    return len(rows)


def trade_row(trade: Any) -> dict[str, Any]:
    data = object_dict(trade)
    exit_time = first(data, "exit_time_ms", "exit_time")
    entry_time = first(data, "entry_time_ms", "entry_time")
    status = first(data, "status")
    if status is None:
        status = "closed" if exit_time not in (None, "") else "open"
    return {
        "trade_id": first(data, "trade_id", "id"),
        "status": status,
        "direction": first(data, "direction", "side"),
        "entry_time_ms": entry_time,
        "exit_time_ms": exit_time,
        "entry_price": first(data, "entry_price"),
        "exit_price": first(data, "exit_price"),
        "qty": first(data, "qty", "quantity", "size"),
        "gross_profit": first(data, "gross_profit", "gross_pnl", "profit"),
        "commission": first(data, "commission", "fee"),
        "net_profit": first(data, "net_profit", "net_pnl"),
        "max_runup": first(data, "max_runup", "mfe"),
        "max_drawdown": first(data, "max_drawdown", "mae"),
    }
