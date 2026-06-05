from __future__ import annotations

from openpine.gateway.live_runner import LiveStrategyRunner, StrategyBarState


def test_live_runner_processes_strictly_after_last_processed_bar() -> None:
    runner = LiveStrategyRunner()
    state = StrategyBarState(strategy_id="strategy-1", last_bar_time_ms=1_000)

    bars = runner._bars_to_process(state, latest_closed_bar_time=4_000, duration_ms=1_000)

    assert bars == [2_000, 3_000, 4_000]


def test_live_runner_legacy_recheck_is_explicit_opt_in() -> None:
    runner = LiveStrategyRunner()
    runner.config.recheck_bars = 1
    state = StrategyBarState(strategy_id="strategy-1", last_bar_time_ms=3_000)

    bars = runner._bars_to_process(state, latest_closed_bar_time=4_000, duration_ms=1_000)

    assert bars == [2_000, 3_000, 4_000]
