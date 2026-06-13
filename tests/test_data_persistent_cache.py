from __future__ import annotations

from pathlib import Path

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    parse_timeframe,
)

from openpine.data.orchestrator import DataOrchestrator


def _query() -> BarQuery:
    return BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        start_ms=0,
        end_ms=120_000,
        source="provider",
        gap_policy="fail",
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
        volume=10.0,
        closed=True,
    )


class _Provider:
    def __init__(self) -> None:
        self.calls = 0

    def fetch_bars(self, query: BarQuery) -> BarSeries:
        self.calls += 1
        bars = (_bar(0), _bar(60_000))
        return BarSeries(
            query=query,
            bars=bars,
            coverage=CoverageReport(
                requested_start_ms=query.start_ms,
                requested_end_ms=query.end_ms,
                delivered_start_ms=0,
                delivered_end_ms=120_000,
                missing_intervals=(),
                source_mix=("provider",),
                status="valid",
            ),
        )


def test_data_orchestrator_reuses_persistent_cache_between_instances(
    tmp_path: Path,
) -> None:
    provider = _Provider()
    first = DataOrchestrator(provider=provider, cache_dir=tmp_path, cache_enabled=True)
    second = DataOrchestrator(provider=provider, cache_dir=tmp_path, cache_enabled=True)

    first_series = first.load_bars(_query())
    second_series = second.load_bars(_query())

    assert provider.calls == 1
    assert [bar.time for bar in first_series.bars] == [0, 60_000]
    assert [bar.time for bar in second_series.bars] == [0, 60_000]


def test_data_orchestrator_can_disable_persistent_cache(tmp_path: Path) -> None:
    provider = _Provider()
    first = DataOrchestrator(provider=provider, cache_dir=tmp_path, cache_enabled=False)
    second = DataOrchestrator(
        provider=provider, cache_dir=tmp_path, cache_enabled=False
    )

    first.load_bars(_query())
    second.load_bars(_query())

    assert provider.calls == 2
