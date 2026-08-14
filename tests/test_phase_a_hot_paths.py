from __future__ import annotations

import asyncio
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    StoreResult,
    parse_timeframe,
)

from openpine.data.orchestrator import DataOrchestrator
from openpine.data import provider_adapter
from openpine.data.periodic_fetcher import PeriodicBarFetcher, RawMarketKey, RefreshConfig
from openpine.gateway.routes import accounts_data


def _query(
    *,
    symbol: str = "BTCUSDT",
    start_ms: int = 0,
    end_ms: int = 120_000,
    source: str = "auto",
) -> BarQuery:
    return BarQuery(
        instrument=InstrumentKey("binance", "spot", symbol),
        timeframe=parse_timeframe("1m"),
        start_ms=start_ms,
        end_ms=end_ms,
        source=source,
        gap_policy="allow_with_metadata",
    )


def _bar(query: BarQuery, time_ms: int) -> Bar:
    return Bar(
        instrument=query.instrument,
        timeframe=query.timeframe,
        time=time_ms,
        time_close=time_ms + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=3.0,
        closed=True,
    )


def _series(
    query: BarQuery,
    bars: tuple[Bar, ...],
    *,
    source: str,
) -> BarSeries:
    return BarSeries(
        query=query,
        bars=bars,
        coverage=DataOrchestrator.coverage_for_series(query, bars, source),
    )


class _EmptyStore:
    def __init__(self) -> None:
        self.written: list[BarSeries] = []

    def read(self, query: BarQuery) -> BarSeries:
        return _series(query, (), source="storage")

    def write(self, series: BarSeries) -> StoreResult:
        self.written.append(series)
        return StoreResult(success=True, rows_written=len(series.bars))

    def coverage(self, query: BarQuery) -> CoverageReport:
        return self.read(query).coverage


class _PersistingProvider:
    persists_fetches = True

    def __init__(self, series: BarSeries) -> None:
        self.series = series
        self.calls = 0

    def fetch_bars(self, query: BarQuery) -> BarSeries:
        self.calls += 1
        return BarSeries(query=query, bars=self.series.bars, coverage=self.series.coverage)


def test_auto_load_does_not_rewrite_result_owned_by_persisting_provider() -> None:
    query = _query()
    provider = _PersistingProvider(_series(query, (_bar(query, 0), _bar(query, 60_000)), source="provider"))
    store = _EmptyStore()
    orchestrator = DataOrchestrator(provider=provider, store=store, cache_enabled=False)

    loaded = orchestrator.load_bars(query)

    assert [bar.time for bar in loaded.bars] == [0, 60_000]
    assert provider.calls == 1
    assert store.written == []


def test_local_marketdata_provider_is_marked_as_persistence_owner(monkeypatch) -> None:
    canonical_provider = SimpleNamespace()
    monkeypatch.setattr(provider_adapter, "ensure_marketdata_provider_version", lambda: None)
    monkeypatch.setattr(provider_adapter, "create_provider", lambda _config: canonical_provider)

    result = provider_adapter.create_local_marketdata_provider_adapter()

    assert result is canonical_provider
    assert result.persists_fetches is True


def test_periodic_refresh_does_not_rewrite_source_owned_by_provider(monkeypatch) -> None:
    class Orchestrator:
        provider_persists_fetches = True

        def __init__(self) -> None:
            self.written: list[BarSeries] = []

        def latest_bar_time(self, _query: BarQuery) -> None:
            return None

        def store_bars(self, series: BarSeries) -> StoreResult:
            self.written.append(series)
            return StoreResult(success=True, rows_written=len(series.bars))

    orchestrator = Orchestrator()
    fetcher = PeriodicBarFetcher(
        RefreshConfig(lookback_bars=1, source_timeframe="1m"),
        registry=SimpleNamespace(list_strategies=lambda: []),
        orchestrator=orchestrator,
    )
    query = _query(end_ms=60_000, source="provider")
    monkeypatch.setattr(fetcher, "_load_source_bars", lambda *_args: [_bar(query, 0)])

    fetcher._refresh_market_key(
        RawMarketKey("binance", "spot", "BTCUSDT", "trade"),
        [SimpleNamespace(timeframe="1m")],
        now_ms=60_000,
    )

    assert orchestrator.written == []


def test_concurrent_identical_provider_loads_share_one_flight() -> None:
    query = _query(source="provider")

    class BlockingProvider:
        persists_fetches = True

        def __init__(self) -> None:
            self.calls = 0
            self.first_call_started = threading.Event()
            self.release_first_call = threading.Event()
            self.lock = threading.Lock()

        def fetch_bars(self, requested: BarQuery) -> BarSeries:
            with self.lock:
                self.calls += 1
                call_number = self.calls
            if call_number == 1:
                self.first_call_started.set()
                assert self.release_first_call.wait(timeout=2)
            return _series(requested, (_bar(requested, 0), _bar(requested, 60_000)), source="provider")

    provider = BlockingProvider()
    orchestrator = DataOrchestrator(
        provider=provider,
        store=_EmptyStore(),
        cache_enabled=False,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(orchestrator.load_bars, query)
        assert provider.first_call_started.wait(timeout=1)
        second = pool.submit(orchestrator.load_bars, query)
        time.sleep(0.05)
        provider.release_first_call.set()
        results = [first.result(timeout=2), second.result(timeout=2)]

    assert provider.calls == 1
    assert [[bar.time for bar in result.bars] for result in results] == [
        [0, 60_000],
        [0, 60_000],
    ]


def test_different_ranges_for_one_series_do_not_fetch_concurrently() -> None:
    first_query = _query(start_ms=0, end_ms=60_000, source="provider")
    second_query = _query(start_ms=60_000, end_ms=120_000, source="provider")

    class BlockingProvider:
        persists_fetches = True

        def __init__(self) -> None:
            self.calls = 0
            self.active = 0
            self.max_active = 0
            self.first_call_started = threading.Event()
            self.release_first_call = threading.Event()
            self.lock = threading.Lock()

        def fetch_bars(self, requested: BarQuery) -> BarSeries:
            with self.lock:
                self.calls += 1
                call_number = self.calls
                self.active += 1
                self.max_active = max(self.max_active, self.active)
            try:
                if call_number == 1:
                    self.first_call_started.set()
                    assert self.release_first_call.wait(timeout=2)
                return _series(requested, (_bar(requested, requested.start_ms),), source="provider")
            finally:
                with self.lock:
                    self.active -= 1

    provider = BlockingProvider()
    orchestrator = DataOrchestrator(
        provider=provider,
        store=_EmptyStore(),
        cache_enabled=False,
    )
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(orchestrator.load_bars, first_query)
        assert provider.first_call_started.wait(timeout=1)
        second = pool.submit(orchestrator.load_bars, second_query)
        time.sleep(0.05)
        provider.release_first_call.set()
        assert first.result(timeout=2).bars[0].time == 0
        assert second.result(timeout=2).bars[0].time == 60_000

    assert provider.calls == 2
    assert provider.max_active == 1


def test_stale_data_summary_is_returned_while_single_background_refresh_runs(
    monkeypatch,
) -> None:
    state = SimpleNamespace(config=SimpleNamespace())
    key = accounts_data._data_summary_cache_key(state)
    old_payload = {"series_count": 1}
    refreshed_payload = {"series_count": 2}
    accounts_data._DATA_SUMMARY_CACHE = (key, time.monotonic() - 60.0, old_payload)
    accounts_data._DATA_SUMMARY_REFRESHING.clear()
    release = threading.Event()
    calls = 0

    def slow_summary(_state):
        nonlocal calls
        calls += 1
        release.wait(timeout=1)
        return refreshed_payload

    monkeypatch.setattr(accounts_data, "_data_summary", slow_summary)
    started = time.monotonic()
    result = accounts_data._data_summary_cached(state)
    elapsed = time.monotonic() - started

    assert result is old_payload
    assert elapsed < 0.1
    assert calls == 1
    assert accounts_data._data_summary_cached(state) is old_payload
    assert calls == 1
    release.set()
    deadline = time.monotonic() + 1
    while time.monotonic() < deadline:
        cached = accounts_data._DATA_SUMMARY_CACHE
        if cached is not None and cached[2] is refreshed_payload:
            break
        time.sleep(0.01)
    assert accounts_data._DATA_SUMMARY_CACHE is not None
    assert accounts_data._DATA_SUMMARY_CACHE[2] is refreshed_payload


def test_manual_refresh_loads_only_tail_off_the_event_loop(monkeypatch) -> None:
    inventory = {
        "id": "series-1",
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "earliest_ms": 0,
        "latest_ms": 60_000,
        "ranges": [{"from_ms": 0, "to_ms": 120_000}],
        "status": "stale",
    }
    monkeypatch.setattr(accounts_data, "_series_by_id", lambda _state: {"series-1": inventory})
    monkeypatch.setattr(accounts_data.time, "time", lambda: 300.0)
    event_loop_thread = threading.get_ident()

    class Orchestrator:
        def __init__(self) -> None:
            self.query: BarQuery | None = None
            self.thread_id: int | None = None

        def load_bars(self, query: BarQuery) -> BarSeries:
            self.query = query
            self.thread_id = threading.get_ident()
            return _series(query, (), source="provider")

    orchestrator = Orchestrator()

    result = asyncio.run(
        accounts_data.refresh_data_series(
            "series-1",
            SimpleNamespace(orchestrator=orchestrator),
        )
    )

    assert orchestrator.query is not None
    assert orchestrator.query.start_ms == 120_000
    assert orchestrator.query.end_ms == 300_000
    assert orchestrator.thread_id != event_loop_thread
    assert result["from_ms"] == 120_000
