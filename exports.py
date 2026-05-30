"""Normalized OpenPine run exports for external parity checks."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from typing import Any

import pandas as pd


PLOT_COLUMNS = ["bar_time", "bar_index"]

TRADE_COLUMNS = [
    "trade_id",
    "status",
    "direction",
    "entry_time_ms",
    "exit_time_ms",
    "entry_price",
    "exit_price",
    "qty",
    "gross_profit",
    "commission",
    "net_profit",
    "max_runup",
    "max_drawdown",
]

EQUITY_COLUMNS = [
    "bar_time_ms",
    "equity",
    "balance",
    "open_profit",
    "net_profit",
    "drawdown",
    "position_size",
]


@dataclass(frozen=True, slots=True)
class ExportWindow:
    from_ms: int
    to_ms: int

    def __post_init__(self) -> None:
        if self.from_ms >= self.to_ms:
            raise ValueError("export window from_ms must be less than to_ms")

    def contains(self, ts_ms: int | None) -> bool:
        return ts_ms is not None and self.from_ms <= ts_ms < self.to_ms


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
        df = df[df["bar_time"] < to_ms]

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
        row = _trade_row(trade)
        if window is not None:
            status = str(row.get("status") or "").lower()
            if status == "closed":
                if not window.contains(_int_or_none(row.get("exit_time_ms"))):
                    continue
            elif not window.contains(_int_or_none(row.get("entry_time_ms"))):
                continue
        rows.append(row)
    pd.DataFrame(rows, columns=TRADE_COLUMNS).to_csv(output, index=False)
    return len(rows)


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
        row = _equity_row(point)
        if window.contains(_int_or_none(row.get("bar_time_ms"))):
            rows.append(row)
    pd.DataFrame(rows, columns=EQUITY_COLUMNS).to_csv(output, index=False)
    return len(rows)


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


def _first(data: dict[str, Any], *names: str) -> Any:
    for name in names:
        value = data.get(name)
        if value is not None:
            return value
    return None


def _int_or_none(value: Any) -> int | None:
    if value in (None, ""):
        return None
    return int(value)


def _trade_row(trade: Any) -> dict[str, Any]:
    data = _object_dict(trade)
    exit_time = _first(data, "exit_time_ms", "exit_time")
    entry_time = _first(data, "entry_time_ms", "entry_time")
    status = _first(data, "status")
    if status is None:
        status = "closed" if exit_time not in (None, "") else "open"
    return {
        "trade_id": _first(data, "trade_id", "id"),
        "status": status,
        "direction": _first(data, "direction", "side"),
        "entry_time_ms": entry_time,
        "exit_time_ms": exit_time,
        "entry_price": _first(data, "entry_price"),
        "exit_price": _first(data, "exit_price"),
        "qty": _first(data, "qty", "quantity", "size"),
        "gross_profit": _first(data, "gross_profit", "gross_pnl", "profit"),
        "commission": _first(data, "commission", "fee"),
        "net_profit": _first(data, "net_profit", "net_pnl"),
        "max_runup": _first(data, "max_runup", "mfe"),
        "max_drawdown": _first(data, "max_drawdown", "mae"),
    }


def _equity_row(point: Any) -> dict[str, Any]:
    data = _object_dict(point)
    return {
        "bar_time_ms": _first(data, "bar_time_ms", "bar_time", "time", "timestamp"),
        "equity": _first(data, "equity"),
        "balance": _first(data, "balance", "cash"),
        "open_profit": _first(data, "open_profit", "unrealized_pnl"),
        "net_profit": _first(data, "net_profit", "net_pnl"),
        "drawdown": _first(data, "drawdown"),
        "position_size": _first(data, "position_size", "position_qty"),
    }
