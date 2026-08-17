from __future__ import annotations

from types import SimpleNamespace

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe

from openpine.registry.strategies import StrategyInstance
from openpine.workers.strategy_job_executor import StrategyJobExecutor


def _strategy() -> StrategyInstance:
    strategy = StrategyInstance(
        strategy_id="strategy-1",
        name="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_json="{}",
        params_hash="params-1",
        symbol="BTCUSDT",
        timeframe="15m",
        exchange="binance",
        market_type="spot",
        price_type="trade",
        mode="paper",
        enabled=True,
    )
    strategy.semantic_profile = "strict_5x"
    return strategy


def _bar(open_time: int = 0) -> Bar:
    tf = parse_timeframe("15m")
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=tf,
        time=open_time,
        time_close=open_time + (tf.duration_ms or 0),
        open=100.0,
        high=110.0,
        low=90.0,
        close=105.0,
        volume=42.0,
        closed=True,
    )


class _Adapter:
    def __init__(self) -> None:
        self.sources: list[bytes] = []

    def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
        self.sources.append(source)
        return SimpleNamespace(ok=True)


def test_job_executor_stamps_source_once_across_bars(monkeypatch) -> None:
    captures: list[tuple] = []

    def capture(*args, **kwargs):
        captures.append((args, kwargs))
        return b"STAMPED"

    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        capture,
    )
    adapter = _Adapter()
    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=adapter,
    )
    strategy = _strategy()
    executor._run_strategy(strategy, _bar(0), None)
    executor._run_strategy(strategy, _bar(900_000), None)
    assert captures == [(("pine-1", "artifact-1"), {})]
    assert adapter.sources == [b"STAMPED", b"STAMPED"]


def test_job_executor_forwards_confirmed_htf_bars(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
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
            return SimpleNamespace(ok=True)

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=Adapter(),
        htf_bars=htf_bars,
    )
    executor._run_strategy(_strategy(), _bar(0), None)
    assert seen["htf_bars"] == htf_bars


def test_job_executor_stamps_confirmed_provider_htf_bars(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(ok=True)

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=Adapter(),
    )
    executor._run_strategy(_strategy(), _bar(0), None)
    assert seen["htf_bars"] == [
        {
            "symbol": "BTCUSDT",
            "timeframe": "15m",
            "time": 0,
            "time_close": 900_000,
            "open": 100.0,
            "high": 110.0,
            "low": 90.0,
            "close": 105.0,
            "volume": 42.0,
        }
    ]


def test_job_executor_does_not_invent_time_close(monkeypatch) -> None:
    monkeypatch.setattr(
        "openpine.workers.strategy_job_executor.capture_generated_source",
        lambda *a, **k: b"STAMPED",
    )
    seen: dict[str, object] = {}

    class Adapter:
        def run_isolated(self, source, bars, config, resume_state=None, htf_bars=None):
            seen["htf_bars"] = htf_bars
            return SimpleNamespace(ok=True)

    executor = StrategyJobExecutor(
        registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        scheduler=SimpleNamespace(),
        state_store=SimpleNamespace(),
        runtime_adapter=Adapter(),
    )
    bar = _bar(0)
    object.__setattr__(bar, "time_close", None)
    executor._run_strategy(_strategy(), bar, None)
    assert seen["htf_bars"] is None
