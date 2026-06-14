from __future__ import annotations

from marketdata_provider.contracts import Bar, BarSeries, InstrumentKey
from openpine.data.orchestrator import DataOrchestrator

from openpine.data.periodic_fetcher import (
    PeriodicBarFetcher,
    RawMarketKey,
    RefreshConfig,
    _group_strategies_by_market,
)
from openpine.registry.strategies import StrategyInstance


def _strategy(
    strategy_id: str, symbol: str, timeframe: str = "15m"
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
        enabled=True,
    )


class _Registry:
    def __init__(self, strategies: list[StrategyInstance]) -> None:
        self._strategies = strategies

    def list_strategies(self):
        return list(self._strategies)


class _Orchestrator:
    def __init__(self) -> None:
        self.queries = []
        self.closed = []
        self.stored = []
        self.latest_time = None

    def get_bars(self, query):
        self.queries.append(query)
        step = query.timeframe.duration_ms or 60_000
        return [
            Bar(
                instrument=query.instrument,
                timeframe=query.timeframe,
                time=current,
                time_close=current + step,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=3.0,
                closed=True,
            )
            for current in range(query.start_ms, query.end_ms, step)
        ]

    def load_bars(self, query):
        bars = tuple(self.get_bars(query))
        return BarSeries(
            query=query,
            bars=bars,
            coverage=DataOrchestrator.coverage_for_series(query, bars, "test"),
        )

    def latest_bar_time(self, query):
        return self.latest_time

    def on_candle_closed(
        self, bar, *, instrument_key: str, timeframe: str, source: str
    ):
        self.closed.append((bar, instrument_key, timeframe, source))

    def store_bars(self, series):
        self.stored.append(series)


def test_group_strategies_by_market_ignores_strategy_timeframe() -> None:
    btc_a = _strategy("btc-a", "BTCUSDT")
    btc_b = _strategy("btc-b", "btcusdt", timeframe="1h")
    sol = _strategy("sol", "SOLUSDT")

    groups = _group_strategies_by_market([btc_a, btc_b, sol])

    assert len(groups) == 2
    assert len(groups[RawMarketKey("binance", "spot", "BTCUSDT", "trade")]) == 2
    assert len(groups[RawMarketKey("binance", "spot", "SOLUSDT", "trade")]) == 1


def test_periodic_fetcher_fetches_once_per_stream_key(monkeypatch) -> None:
    registry = _Registry(
        [
            _strategy("btc-a", "BTCUSDT"),
            _strategy("btc-b", "BTCUSDT", timeframe="1h"),
            _strategy("sol", "SOLUSDT"),
        ]
    )
    orchestrator = _Orchestrator()
    fetcher = PeriodicBarFetcher(
        config=RefreshConfig(lookback_bars=2),
        registry=registry,
        orchestrator=orchestrator,
    )

    monkeypatch.setattr(
        "openpine.data.periodic_fetcher.time.time", lambda: 1_700_000_000.0
    )

    def fake_fetch(key, timeframe, start_ms, end_ms):
        instrument = InstrumentKey(
            exchange=key.exchange, market=key.market_type, symbol=key.symbol
        )
        step = timeframe.duration_ms
        return [
            Bar(
                instrument=instrument,
                timeframe=timeframe,
                time=current,
                time_close=current + step,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=3.0,
                closed=True,
            )
            for current in range(start_ms, end_ms, step)
        ]

    monkeypatch.setattr(fetcher, "_load_source_bars", fake_fetch)

    fetcher._refresh_all_active()

    assert [
        (series.query.instrument.symbol, series.query.timeframe.canonical)
        for series in orchestrator.stored
    ] == [
        ("BTCUSDT", "1m"),
        ("BTCUSDT", "15m"),
        ("BTCUSDT", "1h"),
        ("SOLUSDT", "1m"),
        ("SOLUSDT", "15m"),
    ]
    assert [series.query.start_ms for series in orchestrator.stored[:2]] == [
        1_699_996_260_000,
        1_699_995_600_000,
    ]
    assert not orchestrator.closed


def test_periodic_fetcher_uses_injected_orchestrator_provider(monkeypatch) -> None:
    registry = _Registry([_strategy("bybit-btc", "BTCUSD", timeframe="1h")])
    strategy = registry.list_strategies()[0]
    strategy.exchange = "bybit"
    strategy.market_type = "delivery"
    orchestrator = _Orchestrator()
    fetcher = PeriodicBarFetcher(
        config=RefreshConfig(lookback_bars=2),
        registry=registry,
        orchestrator=orchestrator,
    )

    monkeypatch.setattr(
        "openpine.data.periodic_fetcher.time.time", lambda: 1_700_000_000.0
    )

    def fail_factory():
        raise AssertionError("periodic fetch must use the injected orchestrator")

    monkeypatch.setattr(
        "openpine.data.periodic_fetcher.create_local_marketdata_provider_adapter",
        fail_factory,
    )

    fetcher._refresh_all_active()

    assert len(orchestrator.queries) == 2
    provider_query = orchestrator.queries[0]
    assert provider_query.source == "provider"
    assert provider_query.gap_policy == "allow_with_metadata"
    assert provider_query.instrument.exchange == "bybit"
    assert provider_query.instrument.market == "delivery"
    assert provider_query.instrument.symbol == "BTCUSD"
    assert provider_query.timeframe.canonical == "1m"
    assert [series.query.timeframe.canonical for series in orchestrator.stored] == [
        "1m",
        "1h",
    ]


def test_periodic_fetcher_resumes_after_last_stored_bar(monkeypatch) -> None:
    registry = _Registry([_strategy("pepe", "PEPEUSDT", timeframe="1m")])
    orchestrator = _Orchestrator()
    orchestrator.latest_time = 1_699_999_880_000
    fetcher = PeriodicBarFetcher(
        config=RefreshConfig(lookback_bars=2),
        registry=registry,
        orchestrator=orchestrator,
    )

    monkeypatch.setattr(
        "openpine.data.periodic_fetcher.time.time", lambda: 1_700_000_000.0
    )
    calls = []

    def fake_fetch(key, timeframe, start_ms, end_ms):
        calls.append((start_ms, end_ms))
        return []

    monkeypatch.setattr(fetcher, "_load_source_bars", fake_fetch)

    fetcher._refresh_all_active()

    assert calls == [(1_699_999_940_000, 1_699_999_980_000)]


def test_periodic_fetcher_skips_fetch_when_storage_is_current(monkeypatch) -> None:
    registry = _Registry([_strategy("pepe", "PEPEUSDT", timeframe="1m")])
    orchestrator = _Orchestrator()
    orchestrator.latest_time = 1_699_999_920_000
    fetcher = PeriodicBarFetcher(
        config=RefreshConfig(lookback_bars=2),
        registry=registry,
        orchestrator=orchestrator,
    )

    monkeypatch.setattr(
        "openpine.data.periodic_fetcher.time.time", lambda: 1_700_000_000.0
    )
    calls = []
    monkeypatch.setattr(
        fetcher, "_load_source_bars", lambda *args: calls.append(args) or []
    )

    fetcher._refresh_all_active()

    assert calls == []
