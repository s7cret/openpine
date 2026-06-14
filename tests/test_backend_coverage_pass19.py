from __future__ import annotations

import json
from types import SimpleNamespace
from pathlib import Path

import pytest

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe
from openpine.data import periodic_fetcher
from openpine.data.parallel_fetcher import FetchJob, ParallelDataFetcher, ParallelFetchError, _default_workers
from openpine import exchange_metadata


def _bar(time: int, value: float = 1.0, tf: str = "1m") -> Bar:
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe(tf)
    return Bar(
        instrument=instrument,
        timeframe=timeframe,
        time=time,
        time_close=time + (timeframe.duration_ms or 60_000),
        open=value,
        high=value + 1,
        low=value - 1,
        close=value + 0.5,
        volume=10.0,
        closed=True,
    )


def _strategy(**kwargs):
    defaults = dict(exchange="binance", market_type="spot", symbol="btcusdt", price_type="trade", timeframe="15m", enabled=True)
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


class Orchestrator:
    def __init__(self, latest=None, load_bars=(), fail_store: Exception | None = None):
        self.latest = latest
        self.loaded = tuple(load_bars)
        self.fail_store = fail_store
        self.stored = []
    def latest_bar_time(self, query):
        self.last_latest_query = query
        return self.latest
    def load_bars(self, query):
        self.last_load_query = query
        return SimpleNamespace(bars=self.loaded)
    def get_bars(self, query):
        return list(self.loaded)
    def store_bars(self, series):
        self.stored.append(series)
        if self.fail_store is not None:
            raise self.fail_store


def test_periodic_fetcher_grouping_refresh_and_aggregates(monkeypatch):
    key = periodic_fetcher.RawMarketKey.from_strategy(_strategy(symbol="ethusdt", market_type="SPOT"))
    assert key.instrument_key == "binance:spot:ETHUSDT:trade"
    groups = periodic_fetcher._group_strategies_by_market([
        _strategy(symbol="BTCUSDT", timeframe="15m"),
        _strategy(symbol="BTCUSDT", timeframe="1h"),
        _strategy(symbol="ETHUSDT", timeframe="15m"),
    ])
    assert sorted(len(v) for v in groups.values()) == [1, 2]

    bars = [_bar(0, 1), _bar(60_000, 2), _bar(120_000, 3), _bar(180_000, 4), _bar(240_000, 5), _bar(300_000, 6), _bar(360_000, 7), _bar(420_000, 8), _bar(480_000, 9), _bar(540_000, 10), _bar(600_000, 11), _bar(660_000, 12), _bar(720_000, 13), _bar(780_000, 14), _bar(840_000, 15)]
    orch = Orchestrator(latest=None, load_bars=bars)
    fetcher = periodic_fetcher.PeriodicBarFetcher(
        config=periodic_fetcher.RefreshConfig(interval_seconds=0.01, lookback_bars=2, source_timeframe="1m"),
        registry=SimpleNamespace(list_strategies=lambda: []),
        orchestrator=orch,
    )
    monkeypatch.setattr(fetcher, "_load_source_bars", lambda key, timeframe, start_ms, end_ms: list(bars))
    fetcher._refresh_market_key(
        periodic_fetcher.RawMarketKey("binance", "spot", "BTCUSDT", "trade"),
        [_strategy(timeframe="15m"), _strategy(timeframe="1m")],
        now_ms=900_000,
    )
    assert len(orch.stored) >= 1
    assert fetcher._latest_stored_bar_time(periodic_fetcher.RawMarketKey("binance", "spot", "BTCUSDT", "trade"), parse_timeframe("1m"), 900_000) is None
    # No new bars when latest is already at/after the current close boundary.
    fetcher.orchestrator = Orchestrator(latest=900_000)
    monkeypatch.setattr(fetcher, "_load_source_bars", lambda *a, **k: pytest.fail("should not fetch"))
    fetcher._refresh_market_key(periodic_fetcher.RawMarketKey("binance", "spot", "BTCUSDT", "trade"), [_strategy()], now_ms=900_000)


def test_periodic_fetcher_error_paths_and_provider_load(monkeypatch):
    fetcher = periodic_fetcher.PeriodicBarFetcher(
        config=periodic_fetcher.RefreshConfig(enabled=True, source_timeframe="1M"),
        registry=SimpleNamespace(list_strategies=lambda: []),
        orchestrator=Orchestrator(),
    )
    with pytest.raises(ValueError):
        fetcher._refresh_market_key(periodic_fetcher.RawMarketKey("binance", "spot", "BTCUSDT", "trade"), [_strategy()], now_ms=900_000)

    class FailingLoadOrchestrator(Orchestrator):
        def load_bars(self, query):
            raise RuntimeError("offline")

    failing_fetcher = periodic_fetcher.PeriodicBarFetcher(
        registry=SimpleNamespace(list_strategies=lambda: []),
        orchestrator=FailingLoadOrchestrator(),
    )
    assert failing_fetcher._load_source_bars(
        periodic_fetcher.RawMarketKey("binance", "spot", "BTCUSDT", "trade"), parse_timeframe("1m"), 0, 1
    ) == []

    bybit_orch = Orchestrator(
        load_bars=(
            Bar(
                instrument=InstrumentKey("bybit", "delivery", "BTCUSD"),
                timeframe=parse_timeframe("1m"),
                time=0,
                time_close=60_000,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=9.0,
                closed=True,
            ),
        )
    )
    bybit_fetcher = periodic_fetcher.PeriodicBarFetcher(
        registry=SimpleNamespace(list_strategies=lambda: []),
        orchestrator=bybit_orch,
    )
    out = bybit_fetcher._load_source_bars(
        periodic_fetcher.RawMarketKey("bybit", "delivery", "BTCUSD", "trade"), parse_timeframe("1m"), 0, 60_000
    )
    assert len(out) == 1 and out[0].close == 1.5
    assert bybit_orch.last_load_query.source == "provider"
    assert bybit_orch.last_load_query.gap_policy == "allow_with_metadata"
    assert bybit_orch.last_load_query.instrument.exchange == "bybit"
    assert bybit_orch.last_load_query.instrument.market == "delivery"
    assert bybit_orch.last_load_query.instrument.symbol == "BTCUSD"

    calls = {"n": 0}
    def refresh():
        calls["n"] += 1
        raise RuntimeError("boom")
    fetcher = periodic_fetcher.PeriodicBarFetcher(
        config=periodic_fetcher.RefreshConfig(interval_seconds=0.01),
        registry=SimpleNamespace(list_strategies=lambda: []),
        orchestrator=Orchestrator(),
    )
    fetcher._refresh_all_active = refresh
    fetcher._running = True
    fetcher._stop_event.set()
    fetcher._run_loop()
    assert calls["n"] == 0


def test_parallel_fetcher_chunk_merge_progress_and_errors(monkeypatch):
    assert _default_workers() >= 1
    pf = ParallelDataFetcher(max_workers=2)
    pf._orchestrator = Orchestrator(load_bars=[_bar(0), _bar(60_000)])
    assert [bar.time for bar in pf.fetch_single("BTCUSDT", "1m", 0, 120_000)] == [0, 60_000]
    progress = []
    results = pf.fetch_many([FetchJob("BTCUSDT", "1m", 0, 60_000), FetchJob("ETHUSDT", "1m", 0, 60_000)], lambda key, done, total: progress.append((key, done, total)))
    assert set(results) == {"BTCUSDT:1m", "ETHUSDT:1m"} and progress[-1][2] == 2
    assert pf.fetch_many([]) == {}
    pf.fetch_single = lambda *a, **k: [_bar(60_000), _bar(0), _bar(60_000)]  # type: ignore[method-assign]
    assert [bar.time for bar in pf.fetch_chunked("BTCUSDT", "1m", 0, 120_000, chunk_size_ms=60_000)] == [0, 60_000]
    with pytest.raises(ValueError):
        pf.fetch_chunked("BTCUSDT", "1m", 10, 10)
    pf.fetch_single = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("bad"))  # type: ignore[method-assign]
    with pytest.raises(ParallelFetchError):
        pf.fetch_many([FetchJob("BTCUSDT", "1m", 0, 60_000)])


def test_exchange_metadata_cache_network_and_fallback(monkeypatch, tmp_path: Path):
    exchange_metadata._BINANCE_SPOT_INFO = None
    monkeypatch.setenv("OPENPINE_BINANCE_EXCHANGE_INFO_CACHE", str(tmp_path / "info.json"))
    assert exchange_metadata.default_qty_step("kraken", "spot", "BTCUSDT") is None
    assert exchange_metadata.default_qty_rounding_mode("kraken", "spot", "BTCUSDT") == "none"
    payload = {"symbols": [{"symbol": "ABCUSDT", "filters": [{"filterType": "LOT_SIZE", "stepSize": "0.25"}]}]}
    (tmp_path / "info.json").write_text(json.dumps(payload), encoding="utf-8")
    assert exchange_metadata.default_qty_step("binance", "spot", "ABCUSDT") == 0.25
    assert exchange_metadata.default_qty_rounding_mode("binance", "spot", "ABCUSDT") == "truncate"
    assert exchange_metadata._float_or_none("bad") is None
    assert exchange_metadata._filter({"filters": []}, "LOT_SIZE") is None
    exchange_metadata._BINANCE_SPOT_INFO = None
    (tmp_path / "info.json").unlink()
    class Resp:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self): return json.dumps(payload).encode()
    monkeypatch.setenv("OPENPINE_BINANCE_EXCHANGE_INFO_REFRESH", "1")
    monkeypatch.setattr(exchange_metadata, "urlopen", lambda *a, **k: Resp())
    loaded = exchange_metadata._load_binance_spot_exchange_info(fetch_network=True)
    assert loaded == payload and (tmp_path / "info.json").exists()
    exchange_metadata._BINANCE_SPOT_INFO = None
    (tmp_path / "info.json").write_text("{bad", encoding="utf-8")
    assert exchange_metadata._read_cache(tmp_path / "info.json") is None
