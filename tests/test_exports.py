from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from openpine.export import ExportWindow, export_equity_curve, export_plot_outputs, export_trades


def test_export_plot_outputs_wide_and_filters_window(tmp_path):
    source = tmp_path / "plots.parquet"
    output = tmp_path / "plots.csv"
    pd.DataFrame(
        [
            {"bar_time": 1000, "bar_index": -1, "title": "A", "value": 1.0},
            {"bar_time": 1000, "bar_index": -1, "title": "B", "value": 2.0},
            {"bar_time": 2000, "bar_index": 0, "title": "A", "value": 3.0},
            {"bar_time": 2000, "bar_index": 0, "title": "B", "value": 4.0},
        ]
    ).to_parquet(source)

    rows = export_plot_outputs(source, output, from_ms=2000, to_ms=3000)

    exported = pd.read_csv(output)
    assert rows == 1
    assert list(exported.columns) == ["bar_time", "bar_index", "A", "B"]
    assert exported.to_dict("records") == [
        {"bar_time": 2000, "bar_index": 0, "A": 3.0, "B": 4.0}
    ]


@dataclass
class Trade:
    trade_id: str
    status: str
    direction: str
    entry_time_ms: int
    exit_time_ms: int
    entry_price: float
    exit_price: float
    qty: float
    gross_profit: float
    commission: float
    net_profit: float
    max_runup: float
    max_drawdown: float


def test_export_trades_writes_stable_header(tmp_path):
    output = tmp_path / "trades.csv"
    rows = export_trades(
        [
            Trade(
                trade_id="t1",
                status="closed",
                direction="long",
                entry_time_ms=1000,
                exit_time_ms=2000,
                entry_price=10.0,
                exit_price=11.0,
                qty=2.0,
                gross_profit=2.0,
                commission=0.1,
                net_profit=1.9,
                max_runup=2.5,
                max_drawdown=0.5,
            )
        ],
        output,
        window=ExportWindow(1500, 2500),
    )

    assert rows == 1
    text = output.read_text()
    assert text.splitlines()[0].startswith("trade_id,status,direction,entry_time_ms")
    assert "t1,closed,long,1000,2000" in text


def test_export_trades_filters_closed_by_exit_time(tmp_path):
    output = tmp_path / "trades.csv"

    rows = export_trades(
        [
            {
                "trade_id": "out",
                "status": "closed",
                "direction": "long",
                "entry_time_ms": 1000,
                "exit_time_ms": 1400,
            },
            {
                "trade_id": "in",
                "status": "closed",
                "direction": "long",
                "entry_time_ms": 1000,
                "exit_time_ms": 2000,
            },
        ],
        output,
        window=ExportWindow(1500, 2500),
    )

    exported = pd.read_csv(output)
    assert rows == 1
    assert exported["trade_id"].tolist() == ["in"]


def test_export_equity_curve_filters_visible_window(tmp_path):
    output = tmp_path / "equity_curve.csv"

    rows = export_equity_curve(
        [
            {"bar_time_ms": 1000, "equity": 10_000},
            {"bar_time_ms": 2000, "equity": 10_100},
            {"bar_time_ms": 3000, "equity": 10_200},
        ],
        output,
        window=ExportWindow(1500, 3000),
    )

    exported = pd.read_csv(output)
    assert rows == 1
    assert exported["bar_time_ms"].tolist() == [2000]
