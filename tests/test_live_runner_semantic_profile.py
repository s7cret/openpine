from __future__ import annotations

from types import SimpleNamespace

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.gateway.live_runner import LiveStrategyRunner, RunnerConfig
from openpine.runtime.engine import BacktestRunConfig
from openpine.workers.strategy_job_executor import _build_bar_run_config


class _Strategy:
    strategy_id = "s1"
    artifact_id = "a1"
    params_hash = "h"
    exchange = "BINANCE"
    market_type = "SPOT"
    symbol = "btcusdt"
    timeframe = "1m"
    pine_id = "p1"
    name = "Strategy"
    enabled = True
    status = "running"
    mode = "live"
    semantic_profile = None


def _bar(t: int = 0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, 1.0, 1.0, 1.0, 1.0, 1.0, True)


def _series(start: int, end: int) -> BarSeries:
    bars = (_bar(start), _bar(end - 60_000))
    query = BarQuery(bars[0].instrument, bars[0].timeframe, start, end, gap_policy="allow_with_metadata")
    coverage = CoverageReport(start, end, bars[0].time, bars[-1].time_close, source_mix=("test",))
    return BarSeries(query, bars, coverage)


def test_job_executor_config_requires_strategy_semantic_profile() -> None:
    import pytest
    from openpine_contracts import AdmitError

    with pytest.raises(AdmitError, match="semantic profile"):
        _build_bar_run_config(_Strategy(), _bar())


def test_job_executor_config_forwards_strategy_semantic_profile() -> None:
    strategy = _Strategy()
    strategy.semantic_profile = "strict_5x"
    config = _build_bar_run_config(strategy, _bar())
    assert isinstance(config, BacktestRunConfig)
    assert config.semantic_profile == "strict_5x"
