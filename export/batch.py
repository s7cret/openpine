"""Batch-level strategy export boundary."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from openpine.export.equity import export_equity_curve, initial_equity_at_export_start
from openpine.export.plots import export_plot_records
from openpine.export.trades import export_trades
from openpine.export.window import ExportWindow


@dataclass(frozen=True, slots=True)
class StrategyExportResult:
    """Stable export summary for one strategy run."""

    plots_rows: int
    trades_rows: int
    all_trades_rows: int
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
    all_trades_path = out_dir / "all_trades.csv"
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
    all_trades_rows = export_trades(
        list(getattr(result, "trades", []) or []),
        all_trades_path,
        window=None,
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
        all_trades_rows=all_trades_rows,
        equity_rows=equity_rows,
        outputs={
            "plots": str(plots_path),
            "trades": str(trades_path),
            "all_trades": str(all_trades_path),
            "equity_curve": str(equity_path),
        },
        initial_equity_at_export_start=initial_equity_at_export_start(
            equity_points,
            window,
        ),
    )
