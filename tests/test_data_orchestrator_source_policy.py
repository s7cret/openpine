from __future__ import annotations

import pytest

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey, parse_timeframe
from openpine.data.models import WriteResult
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


class _Storage:
    def __init__(self, bars: tuple[Bar, ...] = (), *, write_success: bool = True) -> None:
        self.bars = bars
        self.write_success = write_success
        self.writes: list[list[Bar]] = []

    def read_candles(self, query: BarQuery) -> list[Bar]:
        return list(self.bars)

    def write_candles(self, candles, **kwargs) -> WriteResult:
        self.writes.append(list(candles))
        if not self.write_success:
            return WriteResult(success=False, error="write failed")
        return WriteResult(success=True, rows_written=len(candles))


class _Provider:
    def __init__(self, bars: tuple[Bar, ...]) -> None:
        self.bars = bars
        self.calls: list[BarQuery] = []

    def fetch_bars(self, query: BarQuery) -> BarSeries:
        self.calls.append(query)
        return BarSeries(
            query=query,
            bars=self.bars,
            coverage=CoverageReport(
                requested_start_ms=query.start_ms,
                requested_end_ms=query.end_ms,
                delivered_start_ms=self.bars[0].time if self.bars else None,
                delivered_end_ms=self.bars[-1].time_close if self.bars else None,
                missing_intervals=() if self.bars else ((query.start_ms, query.end_ms),),
                source_mix=("provider",),
                status="valid" if self.bars else "empty",
            ),
        )


def test_storage_source_fails_on_incomplete_coverage() -> None:
    orchestrator = DataOrchestrator(candle_storage=_Storage((_bar(0),)))

    with pytest.raises(IncompleteCoverageError):
        orchestrator.load_bars(_query(source="storage"))


def test_provider_source_requires_configured_provider() -> None:
    orchestrator = DataOrchestrator(candle_storage=_Storage(()))

    with pytest.raises(ProviderUnavailableError):
        orchestrator.load_bars(_query(source="provider"))


def test_auto_fetches_provider_persists_and_merges_when_storage_incomplete() -> None:
    storage = _Storage((_bar(0),))
    provider = _Provider((_bar(60_000), _bar(120_000)))
    orchestrator = DataOrchestrator(candle_storage=storage, provider=provider)

    series = orchestrator.load_bars(_query(source="auto"))

    assert [bar.time for bar in series.bars] == [0, 60_000, 120_000]
    assert series.coverage.status == "valid"
    assert len(provider.calls) == 1
    assert [[bar.time for bar in write] for write in storage.writes] == [[60_000, 120_000]]


def test_auto_raises_when_provider_write_through_fails() -> None:
    storage = _Storage((_bar(0),), write_success=False)
    provider = _Provider((_bar(60_000), _bar(120_000)))
    orchestrator = DataOrchestrator(candle_storage=storage, provider=provider)

    with pytest.raises(StorageUnavailableError):
        orchestrator.load_bars(_query(source="auto"))
