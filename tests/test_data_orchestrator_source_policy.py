from __future__ import annotations

import pytest

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, StoreResult, parse_timeframe
from openpine.data.orchestrator import (
    DataOrchestrator,
    IncompleteCoverageError,
    ProviderUnavailableError,
    StorageUnavailableError,
)


def _query(source: str = "auto", gap_policy: str = "fail") -> BarQuery:
    return BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        start_ms=0,
        end_ms=180_000,
        source=source,
        gap_policy=gap_policy,
    )


def _bar(time_ms: int) -> Bar:
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        time=time_ms,
        time_close=time_ms + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=None,
        closed=True,
    )


def _open_bar(time_ms: int) -> Bar:
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        time=time_ms,
        time_close=time_ms + 60_000,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=None,
        closed=False,
    )


class _Storage:
    def __init__(self, bars: tuple[Bar, ...] = (), *, write_success: bool = True) -> None:
        self.bars = bars
        self.write_success = write_success
        self.writes: list[list[Bar]] = []

    def read(self, query: BarQuery) -> BarSeries:
        return BarSeries(query=query, bars=self.bars, coverage=_coverage(query, self.bars, "storage"))

    def write(self, series: BarSeries) -> StoreResult:
        self.writes.append(list(series.bars))
        if not self.write_success:
            return StoreResult(success=False, error="write failed")
        return StoreResult(success=True, rows_written=len(series.bars))

    def coverage(self, query: BarQuery) -> CoverageReport:
        return self.read(query).coverage


class _Provider:
    def __init__(self, bars: tuple[Bar, ...]) -> None:
        self.bars = bars
        self.calls: list[BarQuery] = []

    def fetch_bars(self, query: BarQuery) -> BarSeries:
        self.calls.append(query)
        bars = tuple(bar for bar in self.bars if query.start_ms <= bar.time < query.end_ms)
        return BarSeries(
            query=query,
            bars=bars,
            coverage=_coverage(query, bars, "provider"),
        )


def _coverage(query: BarQuery, bars: tuple[Bar, ...], source: str) -> CoverageReport:
    if not bars:
        return CoverageReport(
            requested_start_ms=query.start_ms,
            requested_end_ms=query.end_ms,
            delivered_start_ms=None,
            delivered_end_ms=None,
            missing_intervals=((query.start_ms, query.end_ms),),
            source_mix=(source,),
            status="empty",
        )
    delivered = {bar.time for bar in bars}
    expected = range(query.start_ms, query.end_ms, query.timeframe.duration_ms or query.end_ms)
    missing = tuple((start, start + (query.timeframe.duration_ms or 0)) for start in expected if start not in delivered)
    return CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=bars[-1].time_close,
        missing_intervals=missing,
        source_mix=(source,),
        status="gap" if missing else "valid",
    )


def test_storage_source_fails_on_incomplete_coverage() -> None:
    orchestrator = DataOrchestrator(candle_store=_Storage((_bar(0),)))

    with pytest.raises(IncompleteCoverageError):
        orchestrator.load_bars(_query(source="storage"))


def test_provider_source_requires_configured_provider() -> None:
    orchestrator = DataOrchestrator(candle_store=_Storage(()))

    with pytest.raises(ProviderUnavailableError):
        orchestrator.load_bars(_query(source="provider"))


def test_provider_source_rejects_open_candles() -> None:
    provider = _Provider((_open_bar(0), _bar(60_000), _bar(120_000)))
    orchestrator = DataOrchestrator(candle_store=_Storage(()), provider=provider)

    with pytest.raises(IncompleteCoverageError, match="open candle"):
        orchestrator.load_bars(_query(source="provider"))


def test_auto_fetches_provider_persists_and_merges_when_storage_incomplete() -> None:
    storage = _Storage((_bar(0),))
    provider = _Provider((_bar(60_000), _bar(120_000)))
    orchestrator = DataOrchestrator(candle_store=storage, provider=provider)

    series = orchestrator.load_bars(_query(source="auto"))

    assert [bar.time for bar in series.bars] == [0, 60_000, 120_000]
    assert series.coverage.status == "valid"
    assert [(call.start_ms, call.end_ms) for call in provider.calls] == [
        (60_000, 180_000),
    ]
    assert [[bar.time for bar in write] for write in storage.writes] == [[60_000, 120_000]]


def test_auto_raises_when_provider_write_through_fails() -> None:
    storage = _Storage((_bar(0),), write_success=False)
    provider = _Provider((_bar(60_000), _bar(120_000)))
    orchestrator = DataOrchestrator(candle_store=storage, provider=provider)

    with pytest.raises(StorageUnavailableError):
        orchestrator.load_bars(_query(source="auto"))


def test_candle_closed_write_failure_is_not_returned_as_success_false() -> None:
    orchestrator = DataOrchestrator(candle_store=_Storage((), write_success=False))

    with pytest.raises(StorageUnavailableError, match="write failed"):
        orchestrator.on_candle_closed(
            _bar(0),
            instrument_key="binance:spot:BTCUSDT:trade",
            timeframe="1m",
        )
