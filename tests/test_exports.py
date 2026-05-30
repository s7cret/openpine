from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from openpine.exports import export_plot_outputs, export_trades


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

    rows = export_plot_outputs(source, output, from_ms=2000, to_ms=2000)

    exported = pd.read_csv(output)
    assert rows == 1
    assert list(exported.columns) == ["bar_time", "bar_index", "A", "B"]
    assert exported.to_dict("records") == [
        {"bar_time": 2000, "bar_index": 0, "A": 3.0, "B": 4.0}
    ]


@dataclass
class Trade:
    trade_id: str
    entry_id: str
    exit_id: str
    direction: str
    entry_time: int
    exit_time: int
    entry_price: float
    exit_price: float
    qty: float
    gross_pnl: float
    net_pnl: float
    net_pnl_pct: float
    fee: float
    slippage: float
    bars_held: int
    exit_reason: str


def test_export_trades_writes_stable_header(tmp_path):
    output = tmp_path / "trades.csv"
    rows = export_trades(
        [
            Trade(
                "t1",
                "e1",
                "x1",
                "long",
                1000,
                2000,
                10.0,
                11.0,
                2.0,
                2.0,
                1.9,
                9.5,
                0.1,
                0.0,
                1,
                "close",
            )
        ],
        output,
    )

    assert rows == 1
    text = output.read_text()
    assert text.splitlines()[0].startswith("trade_id,entry_id,exit_id,direction")
    assert "t1,e1,x1,long" in text

