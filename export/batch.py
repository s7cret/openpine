"""Batch-level strategy export boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpine.export import (
    ExportWindow,
    export_equity_curve,
    export_plot_records,
    export_trades,
    _equity_row,
    _int_or_none,
)


@dataclass(frozen=True, slots=True)
class StrategyExportResult:
    """Stable export summary for one strategy run."""

    plots_rows: int
    trades_rows: int
    equity_rows: int
    outputs: dict[str, str]
    initial_equity_at_export_start: Any | None = None


def export_strategy_result(
    *,
    result: Any,
    window: ExportWindow,
    output_dir: str | Path,
) -> StrategyExportResult:
    """Write all strategy outputs for the visible export window.

    The strategy may calculate on a longer prehistory window. This boundary is
    the only place that decides which rows are exported for TV-visible outputs.
    """

    out_dir = Path(output_dir)
    plots_path = out_dir / "plots.csv"
    trades_path = out_dir / "trades.csv"
    equity_path = out_dir / "equity_curve.csv"

    plots = getattr(result, "plots", None)
    if plots is None:
        plot_records: list[Any] = []
    elif isinstance(plots, list):
        plot_records = plots
    elif hasattr(plots, "get_records"):
        plot_records = list(plots.get_records())
    else:
        plot_records = []

    plot_rows = export_plot_records(
        plot_records,
        plots_path,
        from_ms=window.from_ms,
        to_ms=window.to_ms,
    )
    trades_rows = export_trades(
        list(getattr(result, "trades", []) or []),
        trades_path,
        window=window,
    )
    equity_points = list(getattr(result, "equity_curve", None) or [])
    equity_rows = export_equity_curve(
        equity_points,
        equity_path,
        window=window,
    )

    return StrategyExportResult(
        plots_rows=plot_rows,
        trades_rows=trades_rows,
        equity_rows=equity_rows,
        outputs={
            "plots": str(plots_path),
            "trades": str(trades_path),
            "equity_curve": str(equity_path),
        },
        initial_equity_at_export_start=_initial_equity_at_export_start(
            equity_points,
            window,
        ),
    )


def _initial_equity_at_export_start(
    points: list[Any],
    window: ExportWindow,
) -> Any | None:
    selected: tuple[int, Any] | None = None
    for point in points:
        row = _equity_row(point)
        ts_ms = _int_or_none(row.get("bar_time_ms"))
        if ts_ms is None or ts_ms > window.from_ms:
            continue
        if selected is None or ts_ms >= selected[0]:
            selected = (ts_ms, row.get("equity"))
    return None if selected is None else selected[1]
