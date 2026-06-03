from __future__ import annotations

from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe

from openpine.jobs import JobScheduler, JobType
from openpine.registry.strategies import StrategyInstance
from openpine.data.orchestrator import IncompleteCoverageError
from openpine.workers.strategy_fanout import FanoutStatus, StrategyBarFanout, _job_type_for_strategy_mode


def _strategy(
    strategy_id: str,
    *,
    symbol: str = "BTCUSDT",
    timeframe: str = "15m",
    mode: str = "paper",
    enabled: bool = True,
) -> StrategyInstance:
    return StrategyInstance(
        strategy_id=strategy_id,
        name=strategy_id,
        pine_id=f"pine-{strategy_id}",
        artifact_id=f"artifact-{strategy_id}",
        params_json="{}",
        params_hash="hash",
        symbol=symbol,
        timeframe=timeframe,
        exchange="binance",
        market_type="spot",
        price_type="trade",
        mode=mode,
        enabled=enabled,
    )


def _bar(open_time: int, *, symbol: str = "BTCUSDT", timeframe: str = "1m", close: float | None = None) -> Bar:
    tf = parse_timeframe(timeframe)
    value = close if close is not None else 100.5 + open_time / 60_000
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol=symbol),
        timeframe=tf,
        time=open_time,
        time_close=open_time + (tf.duration_ms or 0),
        open=100.0 + open_time / 60_000,
        high=max(101.0 + open_time / 60_000, value),
        low=99.0 + open_time / 60_000,
        close=value,
        volume=1.0,
        closed=True,
    )


class _Registry:
    def __init__(self, strategies: list[StrategyInstance]) -> None:
        self._strategies = strategies

    def list_strategies(self):
        return list(self._strategies)


class _Orchestrator:
    def __init__(self) -> None:
        self.closed = []
        self.bars: dict[tuple[str, str, int], Bar] = {}

    def on_candle_closed(self, bar: Bar, *, instrument_key: str, timeframe: str, source: str):
        self.closed.append((bar, instrument_key, timeframe, source))
        self.bars[(bar.instrument.symbol, timeframe, bar.time)] = bar

    def get_bars(self, query: BarQuery):
        current = query.start_ms
        out = []
        step = query.timeframe.duration_ms or 0
        while current < query.end_ms:
            bar = self.bars.get((query.instrument.symbol, query.timeframe.canonical, current))
            if bar is not None:
                out.append(bar)
            current += step
        return out


class _GapOrchestrator(_Orchestrator):
    def get_bars(self, query: BarQuery):
        raise IncompleteCoverageError("missing source bars")


def test_strategy_fanout_persists_source_and_enqueues_target_jobs() -> None:
    registry = _Registry([
        _strategy("btc-1m", timeframe="1m"),
        _strategy("btc-15m-a", timeframe="15m"),
        _strategy("btc-15m-b", timeframe="15m"),
        _strategy("sol-15m", symbol="SOLUSDT", timeframe="15m"),
    ])
    orchestrator = _Orchestrator()
    scheduler = JobScheduler()
    fanout = StrategyBarFanout(registry=registry, orchestrator=orchestrator, scheduler=scheduler)

    for minute in range(14):
        orchestrator.on_candle_closed(
            _bar(minute * 60_000),
            instrument_key="binance:spot:BTCUSDT:trade",
            timeframe="1m",
            source="live",
        )

    result = fanout.process_source_bar(_bar(14 * 60_000, close=120.0))

    assert result.strategies == 3
    assert {target.timeframe: target.status for target in result.targets} == {
        "15m": FanoutStatus.ENQUEUED,
        "1m": FanoutStatus.ENQUEUED,
    }
    assert len(result.jobs) == 3
    assert len(scheduler.list_jobs()) == 3
    assert [job.job_type for job in scheduler.list_jobs()] == [
        JobType.PAPER_BAR_PROCESS,
        JobType.PAPER_BAR_PROCESS,
        JobType.PAPER_BAR_PROCESS,
    ]
    assert {job.serialization_key for job in scheduler.list_jobs()} == {"btc-1m", "btc-15m-a", "btc-15m-b"}
    assert {job.input["timeframe"] for job in scheduler.list_jobs() if job.input} == {"1m", "15m"}

    persisted = {(item[2], item[3]) for item in orchestrator.closed}
    assert ("1m", "live") in persisted
    assert ("15m", "aggregate") in persisted


def test_strategy_fanout_dedupes_repeated_bar_jobs() -> None:
    registry = _Registry([_strategy("btc-1m", timeframe="1m")])
    orchestrator = _Orchestrator()
    scheduler = JobScheduler()
    fanout = StrategyBarFanout(registry=registry, orchestrator=orchestrator, scheduler=scheduler)
    bar = _bar(0)

    first = fanout.process_source_bar(bar)
    second = fanout.process_source_bar(bar)

    assert len(first.jobs) == 1
    assert len(second.jobs) == 1
    assert first.jobs[0].id == second.jobs[0].id
    assert len(scheduler.list_jobs()) == 1


def test_strategy_fanout_waits_until_target_bar_closed() -> None:
    registry = _Registry([_strategy("btc-15m", timeframe="15m")])
    orchestrator = _Orchestrator()
    scheduler = JobScheduler()
    fanout = StrategyBarFanout(registry=registry, orchestrator=orchestrator, scheduler=scheduler)

    result = fanout.process_source_bar(_bar(60_000))

    assert result.targets[0].status == FanoutStatus.TARGET_NOT_CLOSED
    assert not result.jobs
    assert not scheduler.list_jobs()


def test_strategy_fanout_treats_incomplete_aggregation_window_as_not_ready() -> None:
    registry = _Registry([_strategy("btc-15m", timeframe="15m")])
    scheduler = JobScheduler()
    fanout = StrategyBarFanout(registry=registry, orchestrator=_GapOrchestrator(), scheduler=scheduler)

    result = fanout.process_source_bar(_bar(14 * 60_000))

    assert result.targets[0].status == FanoutStatus.TARGET_NOT_CLOSED
    assert not result.jobs


def test_strategy_fanout_maps_observe_mode_to_observe_jobs() -> None:
    assert _job_type_for_strategy_mode("observe") == JobType.OBSERVE_BAR_PROCESS
    assert _job_type_for_strategy_mode("paper") == JobType.PAPER_BAR_PROCESS
    assert _job_type_for_strategy_mode("live") == JobType.LIVE_BAR_PROCESS
