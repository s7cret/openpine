from __future__ import annotations

from types import SimpleNamespace

import pytest

from openpine.gateway.live_runner import LiveStrategyRunner, StrategyBarState


def test_live_runner_processes_strictly_after_last_processed_bar() -> None:
    runner = LiveStrategyRunner()
    state = StrategyBarState(strategy_id="strategy-1", last_bar_time_ms=1_000)

    bars = runner._bars_to_process(
        state, latest_closed_bar_time=4_000, duration_ms=1_000
    )

    assert bars == [2_000, 3_000, 4_000]


def test_live_runner_legacy_recheck_is_explicit_opt_in() -> None:
    runner = LiveStrategyRunner()
    runner.config.recheck_bars = 1
    state = StrategyBarState(strategy_id="strategy-1", last_bar_time_ms=3_000)

    bars = runner._bars_to_process(
        state, latest_closed_bar_time=4_000, duration_ms=1_000
    )

    assert bars == [2_000, 3_000, 4_000]


def test_live_runner_reads_resume_bar_index_from_object_or_mapping() -> None:
    assert LiveStrategyRunner._resume_bar_index(SimpleNamespace(bar_index=7)) == 7
    assert LiveStrategyRunner._resume_bar_index({"bar_index": "8"}) == 8
    assert LiveStrategyRunner._resume_bar_index({"bar_index": "bad"}) is None


def test_live_runner_requires_runtime_state_for_resume() -> None:
    assert LiveStrategyRunner._resume_has_runtime_state(
        SimpleNamespace(runtime_state={"x": 1})
    )
    assert LiveStrategyRunner._resume_has_runtime_state({"runtime_state": {"x": 1}})
    assert not LiveStrategyRunner._resume_has_runtime_state(
        SimpleNamespace(runtime_state=None)
    )
    assert not LiveStrategyRunner._resume_has_runtime_state(
        {"broker_state": {"position": 0}}
    )


def test_live_runner_attaches_risk_prices_from_pine_inputs() -> None:
    class Storage:
        def execute(self, sql, params):
            return self

        def fetchone(self):
            return (
                'tpPct = input.float(0.70, "Take Profit %")\nslPct = input.float(0.90, "Stop Loss %")',
            )

    runner = LiveStrategyRunner(storage=Storage())
    strategy = SimpleNamespace(strategy_id="strategy-1", pine_id="pine-1")
    orders = [{"side": "buy", "entry_price": 100.0}]

    runner._attach_risk_prices(strategy, orders)

    assert orders[0]["take_profit_price"] == pytest.approx(100.7)
    assert orders[0]["stop_price"] == pytest.approx(99.1)


def test_live_runner_attaches_short_risk_prices_from_pine_inputs() -> None:
    class Storage:
        def execute(self, sql, params):
            return self

        def fetchone(self):
            return (
                'tpPct = input.float(0.70, "Take Profit %")\nslPct = input.float(0.90, "Stop Loss %")',
            )

    runner = LiveStrategyRunner(storage=Storage())
    strategy = SimpleNamespace(strategy_id="strategy-1", pine_id="pine-1")
    orders = [{"side": "sell", "price": 100.0}]

    runner._attach_risk_prices(strategy, orders)

    assert orders[0]["take_profit_price"] == pytest.approx(99.3)
    assert orders[0]["stop_price"] == pytest.approx(100.9)
