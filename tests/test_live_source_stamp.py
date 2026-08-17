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
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
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


def test_live_runner_forwards_confirmed_htf_bars(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.runtime.isolated_run.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    htf_bars = [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40,
            "high": 43,
            "low": 39,
            "close": 42,
            "volume": 1,
        }
    ]
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(
                raw_result=SimpleNamespace(trades=[], order_lifecycle=[]),
                resume_state=None,
            )

    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    runner = LiveStrategyRunner(
        RunnerConfig(lookback_bars=2),
        orchestrator=SimpleNamespace(load_bars=lambda query: _series(query.start_ms, query.end_ms)),
        state_store=None,
        htf_bars=htf_bars,
    )
    assert runner._run_mini_backtest(_Strategy(), 120000) == []
    assert seen["htf_bars"] == htf_bars


def test_live_runner_stamps_confirmed_provider_htf_bars(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.runtime.isolated_run.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(
                raw_result=SimpleNamespace(trades=[], order_lifecycle=[]),
                resume_state=None,
            )

    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    runner = LiveStrategyRunner(
        RunnerConfig(lookback_bars=2),
        orchestrator=SimpleNamespace(load_bars=lambda query: _series(query.start_ms, query.end_ms)),
        state_store=None,
    )
    assert runner._run_mini_backtest(_Strategy(), 120000) == []
    stamped = seen["htf_bars"]
    assert isinstance(stamped, list)
    assert stamped
    assert {item["symbol"] for item in stamped} == {"BTCUSDT"}
    assert {item["timeframe"] for item in stamped} == {"1m"}
    assert all(item["time_close"] is not None for item in stamped)


def test_live_runner_does_not_invent_time_close(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.runtime.isolated_run.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(
                raw_result=SimpleNamespace(trades=[], order_lifecycle=[]),
                resume_state=None,
            )

    inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    tf = parse_timeframe("1m")
    bare = SimpleNamespace(
        instrument=inst,
        timeframe=tf,
        time=0,
        time_close=None,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=1.0,
        closed=True,
    )

    def load_bars(query):
        bars = (bare, SimpleNamespace(**{**bare.__dict__, "time": 60_000}))
        report = CoverageReport(query.start_ms, query.end_ms, 0, 60_000, source_mix=("test",))
        return BarSeries(query, bars, report)

    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    runner = LiveStrategyRunner(
        RunnerConfig(lookback_bars=2),
        orchestrator=SimpleNamespace(load_bars=load_bars),
        state_store=None,
    )
    assert runner._run_mini_backtest(_Strategy(), 120000) == []
    assert seen["htf_bars"] is None


def test_live_runner_fetches_explicit_htf_timeframe(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.runtime.isolated_run.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    seen: dict[str, object] = {}
    loaded: list[str] = []

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(
                raw_result=SimpleNamespace(trades=[], order_lifecycle=[]),
                resume_state=None,
            )

    def load_bars(query):
        loaded.append(str(query.timeframe.canonical))
        if str(query.timeframe.canonical) == "1D":
            inst = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
            tf = parse_timeframe("1D")
            bar = Bar(inst, tf, 0, 86_399_999, 40.0, 43.0, 39.0, 42.0, 1.0, True)
            report = CoverageReport(query.start_ms, query.end_ms, 0, 86_399_999, source_mix=("test",))
            return BarSeries(query, (bar,), report)
        return _series(query.start_ms, query.end_ms)

    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    runner = LiveStrategyRunner(
        RunnerConfig(lookback_bars=2),
        orchestrator=SimpleNamespace(load_bars=load_bars),
        state_store=None,
        htf_timeframe="1D",
    )
    assert runner._run_mini_backtest(_Strategy(), 120000) == []
    assert "1D" in loaded
    assert seen["htf_bars"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "1D",
            "time": 0,
            "time_close": 86_399_999,
            "open": 40.0,
            "high": 43.0,
            "low": 39.0,
            "close": 42.0,
            "volume": 1.0,
        }
    ]


def test_live_runner_same_htf_timeframe_does_not_refetch(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.runtime.isolated_run.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    loaded: list[str] = []

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
            return SimpleNamespace(
                raw_result=SimpleNamespace(trades=[], order_lifecycle=[]),
                resume_state=None,
            )

    def load_bars(query):
        loaded.append(str(query.timeframe.canonical))
        return _series(query.start_ms, query.end_ms)

    monkeypatch.setattr("openpine.runtime.engine.BacktestEngineAdapter", Adapter)
    runner = LiveStrategyRunner(
        RunnerConfig(lookback_bars=2),
        orchestrator=SimpleNamespace(load_bars=load_bars),
        state_store=None,
        htf_timeframe="1m",
    )
    assert runner._run_mini_backtest(_Strategy(), 120000) == []
    assert loaded
    assert all(tf == "1m" for tf in loaded)
