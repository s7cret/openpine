from __future__ import annotations

import sys

import pytest
from backtest_engine import BacktestConfig, BacktestEngine, Bar

from openpine.runtime.isolated_run import IsolatedRunError, run_isolated_artifact


def _bars() -> list[Bar]:
    return [
        Bar(time=1_000 + i, open=10.0 + i, high=11.0 + i, low=9.0 + i, close=10.5 + i)
        for i in range(6)
    ]


def _cfg() -> BacktestConfig:
    return BacktestConfig(
        symbol="S",
        timeframe="1m",
        start_time=1_000,
        end_time=1_005,
        commission_type="none",
        initial_capital=10_000,
        score_start_time=1_000,
        score_end_time=1_005,
    )


def test_isolated_run_replays_live_tape_without_importing_generated() -> None:
    source = (
        "from pinelib.strategy.context import StrategyContext\n"
        "ctx = StrategyContext(intent_run_id='run', intent_strategy_id='s')\n"
        "ctx._runtime = type('RT', (), {"
        "'bar_index': 2, "
        "'current_bar': type('B', (), {'time': 1002})()"
        "})()\n"
        "ctx.entry('L', 'long', qty=1)\n"
    )
    result = run_isolated_artifact(source.encode("utf-8"), bars=_bars(), config=_cfg())
    assert result["intent_tape"][0]["schema_id"] == "openpine.intent.v2"
    assert result["intent_tape"][0]["kind"] == "entry"
    assert result["score_ledger_hash"]
    assert not any(name.startswith("openpine_generated_") for name in sys.modules)

    class LiveEntry:
        def __init__(self, params, runtime, ctx):
            self.ctx = ctx

        def _process_bar(self, bar, bar_index):
            if bar_index == 2:
                self.ctx.entry("L", "long", qty=1.0)

    live = BacktestEngine(_cfg()).run(LiveEntry, bars=_bars())
    assert result["score_ledger_hash"] == live.score_ledger_hash


def test_isolated_run_rejects_artifact_without_tape() -> None:
    with pytest.raises(IsolatedRunError, match="live pinelib tape"):
        run_isolated_artifact(b"VALUE = 1\n", bars=_bars(), config=_cfg())
