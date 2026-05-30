"""Stable CSV schemas for OpenPine exports."""

from __future__ import annotations

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
