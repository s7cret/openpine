from __future__ import annotations

import sys
from types import ModuleType, SimpleNamespace

import pandas as pd
import pytest
from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe

from openpine.data import candle_storage as candle_storage_mod
from openpine.data import periodic_fetcher
from openpine.data import provider_adapter as provider_adapter_mod
from openpine.data.footprint_orchestrator import FootprintOrchestrator
from openpine.data.orchestrator import StorageUnavailableError
from openpine.data.row_helpers import attr_or_item


def _strategy(**overrides):
    values = {
        "strategy_id": "s1",
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "price_type": "trade",
        "timeframe": "1m",
        "enabled": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _bar(time_ms: int, *, timeframe: str = "1m", value: float = 1.0) -> Bar:
    timeframe_obj = parse_timeframe(timeframe)
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=timeframe_obj,
        time=time_ms,
        time_close=time_ms + (timeframe_obj.duration_ms or 60_000),
        open=value,
        high=value + 1.0,
        low=value - 1.0,
        close=value + 0.5,
        volume=10.0,
        closed=True,
    )


def _fetcher(orchestrator, *, registry=None, source_timeframe: str = "1m"):
    return periodic_fetcher.PeriodicBarFetcher(
        config=periodic_fetcher.RefreshConfig(
            interval_seconds=1.0,
            lookback_bars=1,
            source_timeframe=source_timeframe,
        ),
        registry=registry or SimpleNamespace(list_strategies=lambda: []),
        orchestrator=orchestrator,
    )


def test_periodic_run_loop_catches_refresh_exception_and_sleeps(monkeypatch) -> None:
    fetcher = _fetcher(SimpleNamespace())
    calls = []

    def failing_refresh() -> None:
        calls.append("refresh")
        raise RuntimeError("refresh boom")

    def fake_sleep(seconds: float) -> None:
        calls.append(("sleep", seconds))
        fetcher._running = False

    monkeypatch.setattr(fetcher, "_refresh_all_active", failing_refresh)
    monkeypatch.setattr(periodic_fetcher.time, "sleep", fake_sleep)
    fetcher._running = True
    fetcher._stop_event.clear()

    fetcher._run_loop()

    assert calls == ["refresh", ("sleep", 1.0)]


def test_periodic_refresh_all_active_handles_market_errors_and_refresh_strategy(
    monkeypatch,
) -> None:
    registry = SimpleNamespace(
        list_strategies=lambda: [
            _strategy(symbol="btcusdt", timeframe="5m"),
            _strategy(symbol="ETHUSDT", enabled=False),
        ]
    )
    fetcher = _fetcher(SimpleNamespace(), registry=registry)
    calls = []

    def failing_market_refresh(key, group, *, now_ms: int) -> None:
        calls.append((key, tuple(group), now_ms))
        raise RuntimeError("market boom")

    monkeypatch.setattr(fetcher, "_refresh_market_key", failing_market_refresh)
    monkeypatch.setattr(periodic_fetcher.time, "time", lambda: 12.345)

    fetcher._refresh_all_active()

    assert len(calls) == 1
    assert calls[0][0] == periodic_fetcher.RawMarketKey(
        "binance", "spot", "BTCUSDT", "trade"
    )
    assert fetcher.last_fetch_at == 12_345
    assert fetcher.last_fetch_instruments == 1

    captured = {}

    def record_market_refresh(key, group, *, now_ms: int) -> None:
        captured["key"] = key
        captured["group"] = list(group)
        captured["now_ms"] = now_ms

    monkeypatch.setattr(fetcher, "_refresh_market_key", record_market_refresh)
    monkeypatch.setattr(periodic_fetcher.time, "time", lambda: 20.0)

    strategy = _strategy(symbol="ethusdt", market_type="FUTURES")
    fetcher._refresh_strategy(strategy)

    assert captured == {
        "key": periodic_fetcher.RawMarketKey("binance", "futures", "ETHUSDT", "trade"),
        "group": [strategy],
        "now_ms": 20_000,
    }


def test_periodic_refresh_market_key_reraises_non_conflicting_store_errors(
    monkeypatch,
) -> None:
    class FailingStoreOrchestrator:
        def latest_bar_time(self, query):
            return None

        def load_bars(self, query):
            return SimpleNamespace(bars=())

        def store_bars(self, series):
            raise StorageUnavailableError("database down")

    fetcher = _fetcher(FailingStoreOrchestrator())
    monkeypatch.setattr(fetcher, "_fetch_bars_direct", lambda *args: [_bar(0)])

    with pytest.raises(StorageUnavailableError, match="database down"):
        fetcher._refresh_market_key(
            periodic_fetcher.RawMarketKey("binance", "spot", "BTCUSDT", "trade"),
            [_strategy(timeframe="1m")],
            now_ms=120_000,
        )


def test_periodic_target_aggregate_skip_and_reraise_edges() -> None:
    class AggregateOrchestrator:
        def __init__(self) -> None:
            self.stored = []

        def load_bars(self, query):
            return SimpleNamespace(bars=())

        def store_bars(self, series):
            self.stored.append(series)
            raise StorageUnavailableError("aggregate write failed")

    orchestrator = AggregateOrchestrator()
    fetcher = _fetcher(orchestrator)
    key = periodic_fetcher.RawMarketKey("binance", "spot", "BTCUSDT", "trade")

    fetcher._store_target_aggregates(
        key,
        [],
        source_timeframe=parse_timeframe("1M"),
        target_timeframes=["1m"],
    )
    assert orchestrator.stored == []

    fetcher._store_target_aggregates(
        key,
        [_bar(0, timeframe="5m")],
        source_timeframe=parse_timeframe("5m"),
        target_timeframes=["1m"],
    )
    assert orchestrator.stored == []

    source_bars = [_bar(idx * 60_000, value=float(idx + 1)) for idx in range(5)]
    with pytest.raises(StorageUnavailableError, match="aggregate write failed"):
        fetcher._store_target_aggregates(
            key,
            source_bars,
            source_timeframe=parse_timeframe("1m"),
            target_timeframes=["5m"],
        )
    assert orchestrator.stored[-1].query.timeframe.canonical == "5m"


def test_periodic_latest_stored_bar_time_returns_none_on_storage_exception() -> None:
    class RaisingLatestOrchestrator:
        def latest_bar_time(self, query):
            raise RuntimeError("manifest unavailable")

    fetcher = _fetcher(RaisingLatestOrchestrator())

    assert (
        fetcher._latest_stored_bar_time(
            periodic_fetcher.RawMarketKey("binance", "spot", "BTCUSDT", "trade"),
            parse_timeframe("1m"),
            60_000,
        )
        is None
    )


def test_provider_adapter_version_factory_and_normalize_fallbacks(
    monkeypatch,
    tmp_path,
) -> None:
    import marketdata_provider

    monkeypatch.setattr(marketdata_provider, "__version__", "0.0.0", raising=False)
    with pytest.raises(RuntimeError, match="requires marketdata-provider"):
        provider_adapter_mod.ensure_marketdata_provider_version()

    provider = object()
    sentinel = object()
    create_calls = []
    runtime_calls = []

    def fake_create(**kwargs):
        create_calls.append(kwargs)
        return provider

    def fake_runtime(received_provider, *, exchange, market, prefetch_end_ms):
        runtime_calls.append((received_provider, exchange, market, prefetch_end_ms))
        return sentinel

    monkeypatch.setattr(
        provider_adapter_mod, "create_local_marketdata_provider_adapter", fake_create
    )
    monkeypatch.setattr(provider_adapter_mod, "RuntimeDataProviderAdapter", fake_runtime)
    config = object()

    assert (
        provider_adapter_mod.create_local_runtime_data_provider_adapter(
            config=config,
            cache_dir=tmp_path,
            exchange="BINANCE",
            market="FUTURES",
            prefetch_end_ms=123,
        )
        is sentinel
    )
    assert create_calls == [{"config": config, "cache_dir": tmp_path}]
    assert runtime_calls == [(provider, "BINANCE", "FUTURES", 123)]

    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1M"),
        start_ms=1_000,
        end_ms=9_000,
        source="provider",
    )
    normalized = provider_adapter_mod.normalize_provider_bar(
        {
            "open_time_ms": 1_000,
            "open": "1.0",
            "high": "2.0",
            "low": "0.5",
            "close": "1.5",
        },
        query,
    )

    assert normalized.instrument == query.instrument
    assert normalized.time_close == query.end_ms
    assert normalized.volume is None
    assert normalized.closed is True


def test_storage_hashes_footprint_no_store_and_row_helper_missing(monkeypatch) -> None:
    fake_xxhash = ModuleType("xxhash")

    class FakeHash:
        def __init__(self, value) -> None:
            self.value = value

        def hexdigest(self) -> str:
            return f"fake-{len(str(self.value))}"

    fake_xxhash.xxh64 = FakeHash
    monkeypatch.setitem(sys.modules, "xxhash", fake_xxhash)

    assert candle_storage_mod._compute_checksum(pd.DataFrame([{"open_time": 1}])).startswith(
        "fake-"
    )
    assert candle_storage_mod._compute_schema_hash().startswith("fake-")

    with pytest.raises(StorageUnavailableError, match="footprint store is not configured"):
        FootprintOrchestrator().store_footprints(SimpleNamespace())

    with pytest.raises(AttributeError, match="missing any of"):
        attr_or_item({"present": 1}, "missing", "also_missing")
