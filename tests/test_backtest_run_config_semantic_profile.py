from __future__ import annotations

import pytest

from openpine.runtime.engine import BacktestEngineAdapter, BacktestRunConfig
from openpine.runtime.isolated_run import IsolatedRunError


def test_backtest_run_config_has_no_silent_legacy_default() -> None:
    config = BacktestRunConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    )
    assert not config.semantic_profile


def test_run_isolated_rejects_default_backtest_run_config() -> None:
    config = BacktestRunConfig(
        symbol="BTCUSDT",
        timeframe="1m",
        start_time=0,
        end_time=60_000,
    )
    with pytest.raises(IsolatedRunError, match="semantic_profile"):
        BacktestEngineAdapter().run_isolated(b"VALUE = 1\n", [], config)
