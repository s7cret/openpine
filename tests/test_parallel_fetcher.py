from __future__ import annotations

import pytest

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe
from openpine.data.parallel_fetcher import FetchJob, ParallelDataFetcher, ParallelFetchError


def _bar(time: int) -> Bar:
    return Bar(
        instrument=InstrumentKey(exchange="binance", market="usdm", symbol="BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        time=time,
        time_close=time + 60_000,
        open=1.0,
        high=1.0,
        low=1.0,
        close=1.0,
        volume=None,
        closed=True,
    )


def test_parallel_fetcher_fails_closed_on_job_error() -> None:
    fetcher = ParallelDataFetcher.__new__(ParallelDataFetcher)
    fetcher.max_workers = 1

    def fail_fetch_single(*_args, **_kwargs):
        raise RuntimeError("provider unavailable")

    fetcher.fetch_single = fail_fetch_single

    with pytest.raises(ParallelFetchError, match="provider unavailable"):
        fetcher.fetch_many([FetchJob("BTCUSDT", "1m", 1, 2)])


def test_chunked_fetch_deduplicates_canonical_bar_time() -> None:
    fetcher = ParallelDataFetcher.__new__(ParallelDataFetcher)
    fetcher.max_workers = 1
    fetcher.fetch_many = lambda _jobs: {
        "chunk-a": [_bar(1), _bar(2)],
        "chunk-b": [_bar(2), _bar(3)],
    }

    bars = fetcher.fetch_chunked("BTCUSDT", "1m", 1, 3, chunk_size_ms=1)

    assert [bar.time for bar in bars] == [1, 2, 3]
