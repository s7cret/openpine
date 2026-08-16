from __future__ import annotations

from types import SimpleNamespace

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe

from openpine.gateway.live_runner import LiveStrategyRunner, RunnerConfig


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
    semantic_profile = "strict_5x"


def _bar(t: int = 0) -> Bar:
    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    return Bar(inst, tf, t, t + 60_000, 1.0, 1.0, 1.0, 1.0, 1.0, True)


def _series(start: int, end: int) -> BarSeries:
    bars = (_bar(start), _bar(end - 60_000))
    query = BarQuery(bars[0].instrument, bars[0].timeframe, start, end, gap_policy="allow_with_metadata")
    coverage = CoverageReport(start, end, bars[0].time, bars[-1].time_close, source_mix=("test",))
    return BarSeries(query, bars, coverage)


def test_live_runner_stamps_source_once_across_ticks(monkeypatch) -> None:
    captures: list[tuple] = []
    seen: list[bytes] = []

    def capture(*args, **kwargs):
        captures.append((args, kwargs))
        return b"STAMPED"

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None):
            seen.append(source)
            return SimpleNamespace(
                raw_result=SimpleNamespace(trades=[], order_lifecycle=[]),
                resume_state=None,
            )

    monkeypatch.setattr("openpine.runtime.isolated_run.capture_generated_source", capture)
    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    runner = LiveStrategyRunner(
        RunnerConfig(lookback_bars=2),
        orchestrator=SimpleNamespace(load_bars=lambda query: _series(query.start_ms, query.end_ms)),
        state_store=None,
    )
    strategy = _Strategy()
    assert runner._run_mini_backtest(strategy, 120000) == []
    assert runner._run_mini_backtest(strategy, 180000) == []
    assert captures == [(("p1", "a1"), {})]
    assert seen == [b"STAMPED", b"STAMPED"]
