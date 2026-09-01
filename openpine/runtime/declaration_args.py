"""Helpers for Pine strategy declaration arguments used by runtime adapters."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any


_STRATEGY_DECLARATION_DEFAULTS: dict[str, Any] = {
    "initial_capital": 1_000_000.0,
    "currency": "USD",
    "default_qty_type": "fixed",
    "default_qty_value": 1.0,
    "pyramiding": 1,
    "commission_type": "percent",
    "commission_value": 0.0,
    "slippage": 0.0,
    "process_orders_on_close": False,
    "calc_on_order_fills": False,
    "calc_on_every_tick": False,
    "use_bar_magnifier": False,
    "backtest_fill_limits_assumption": None,
    "close_entries_rule": "FIFO",
    "margin_long": 100.0,
    "margin_short": 100.0,
    "fill_orders_on_standard_ohlc": None,
    "risk_free_rate": 0.0,
    "max_bars_back": None,
    "max_lines_count": None,
    "max_labels_count": None,
    "max_boxes_count": None,
    "strict_tv_parity": False,
    "qty_step": None,
    "qty_rounding_mode": "none",
    "metadata": {},
}


def strategy_declaration_defaults() -> dict[str, Any]:
    """Return detached Pine ``strategy()`` declaration defaults."""

    return {
        key: dict(value) if isinstance(value, dict) else value
        for key, value in _STRATEGY_DECLARATION_DEFAULTS.items()
    }


def normalize_strategy_declaration_args(
    decl_args: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Merge artifact declaration args over Pine strategy() defaults.

    AST2Python/Pinelib generated strategies validate the runtime config against
    the effective strategy declaration. When Pine source omits optional
    strategy() arguments, generated code uses Pinelib's StrategyDeclaration
    defaults, not OpenPine gateway fallback constants. Keep every caller on the
    same defaults so omitted fields such as initial_capital and pyramiding do
    not create generated declaration/config mismatches at runtime.
    """

    merged = strategy_declaration_defaults()
    if decl_args:
        merged.update(dict(decl_args))
    return merged


def artifact_strategy_declaration_args(artifact: Mapping[str, Any] | None) -> dict[str, Any]:
    """Extract and normalize strategy declaration args from an artifact dict."""

    compile_meta = (artifact or {}).get("compile_meta", {})
    declaration = compile_meta.get("translation_metadata", {}).get("declaration", {})
    return normalize_strategy_declaration_args(declaration.get("arguments", {}))
