from __future__ import annotations

from marketdata_provider.contracts import Bar

from openpine.data.periodic_fetcher import (
    PeriodicBarFetcher,
    RawMarketKey,
    RefreshConfig,
    _group_strategies_by_market,
)
from openpine.registry.strategies import StrategyInstance


def _strategy(strategy_id: str, symbol: str, timeframe: str = "15m") -> StrategyInstance:
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

    def get_bars(self, query):
        self.queries.append(query)
        return [
            Bar(
                instrument=query.instrument,
                timeframe=query.timeframe,
                time=query.start_ms,
                time_close=query.start_ms + query.timeframe.duration_ms,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=3.0,
                closed=True,
            )
        ]

    def on_candle_closed(self, bar, *, instrument_key: str, timeframe: str, source: str):
        self.closed.append((bar, instrument_key, timeframe, source))


def test_group_strategies_by_market_ignores_strategy_timeframe() -> None:
    btc_a = _strategy("btc-a", "BTCUSDT")
    btc_b = _strategy("btc-b", "btcusdt", timeframe="1h")
    sol = _strategy("sol", "SOLUSDT")

    groups = _group_strategies_by_market([btc_a, btc_b, sol])

    assert len(groups) == 2
    assert len(groups[RawMarketKey("binance", "spot", "BTCUSDT", "trade")]) == 2
    assert len(groups[RawMarketKey("binance", "spot", "SOLUSDT", "trade")]) == 1


def test_periodic_fetcher_fetches_once_per_stream_key(monkeypatch) -> None:
    registry = _Registry([
        _strategy("btc-a", "BTCUSDT"),
        _strategy("btc-b", "BTCUSDT", timeframe="1h"),
        _strategy("sol", "SOLUSDT"),
    ])
    orchestrator = _Orchestrator()
    fetcher = PeriodicBarFetcher(
        config=RefreshConfig(lookback_bars=2),
        registry=registry,
        orchestrator=orchestrator,
    )

    monkeypatch.setattr("openpine.data.periodic_fetcher.time.time", lambda: 1_700_000_000.0)

    fetcher._refresh_all_active()

    assert [query.instrument.symbol for query in orchestrator.queries] == ["BTCUSDT", "SOLUSDT"]
    assert [query.timeframe.canonical for query in orchestrator.queries] == ["1m", "1m"]
    assert [query.start_ms for query in orchestrator.queries] == [1_699_999_860_000, 1_699_999_860_000]
    assert [query.end_ms for query in orchestrator.queries] == [1_699_999_980_000, 1_699_999_980_000]
    assert {item[1] for item in orchestrator.closed} == {
        "binance:spot:BTCUSDT:trade",
        "binance:spot:SOLUSDT:trade",
    }
    assert all(item[2] == "1m" and item[3] == "live" for item in orchestrator.closed)
