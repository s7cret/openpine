"""ParallelDataFetcher — multi-threaded historical bar loading.

Supports:
- Parallel fetch of multiple symbols/timeframes
- Chunked parallel fetch for large date ranges (Binance 1500 bar limit)
- Auto worker count: half CPU cores by default
"""

from __future__ import annotations

import os
import structlog
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from typing import Callable

from marketdata_provider.contracts import Bar, BarQuery, InstrumentKey, parse_timeframe
from openpine.data.orchestrator import DataOrchestrator
from openpine.data.provider_adapter import create_local_marketdata_provider_adapter

log = structlog.get_logger(__name__)


def _default_workers() -> int:
    """Default: half of CPU cores, minimum 1."""
    return max(1, os.cpu_count() // 2)


@dataclass(frozen=True)
class FetchJob:
    """Single fetch job specification."""

    symbol: str
    timeframe: str
    start_ms: int | None
    end_ms: int | None
    exchange: str = "binance"
    market_type: str = "usdm"


class ParallelFetchError(RuntimeError):
    """Raised when a parallel fetch job fails."""


class ParallelDataFetcher:
    """Multi-threaded bar fetcher with automatic chunking.

    Usage:
        fetcher = ParallelDataFetcher(max_workers=4)
        bars = fetcher.fetch_single("BTCUSDT", "15m", start_ms, end_ms)
        results = fetcher.fetch_many([
            FetchJob("BTCUSDT", "15m", start, end),
            FetchJob("ETHUSDT", "15m", start, end),
        ])
    """

    def __init__(self, max_workers: int | None = None) -> None:
        self.max_workers = max_workers or _default_workers()
        provider = create_local_marketdata_provider_adapter()
        self._orchestrator = DataOrchestrator(provider=provider)
        log.info(
            "parallel_fetcher.init",
            max_workers=self.max_workers,
            provider_set=True,
        )

    def fetch_single(
        self,
        symbol: str,
        timeframe: str,
        start_ms: int | None = None,
        end_ms: int | None = None,
        exchange: str = "binance",
    ) -> list[Bar]:
        """Fetch bars for a single symbol/timeframe."""
        query = BarQuery(
            instrument=InstrumentKey(exchange=exchange, market="usdm", symbol=symbol),
            timeframe=parse_timeframe(timeframe),
            start_ms=start_ms if start_ms is not None else 0,
            end_ms=end_ms if end_ms is not None else int(10**18),
            source="provider",
        )
        return self._orchestrator.get_bars(query)

    def fetch_many(
        self,
        jobs: list[FetchJob],
        progress_callback: Callable[[str, int, int], None] | None = None,
    ) -> dict[str, list[Bar]]:
        """Fetch bars for multiple jobs in parallel.

        Returns dict mapping "SYMBOL:TIMEFRAME" -> list[Bar].
        """
        if not jobs:
            return {}

        results: dict[str, list[Bar]] = {}
        completed = 0
        total = len(jobs)

        log.info("parallel_fetcher.start", jobs=total, workers=self.max_workers)

        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            future_to_key: dict = {}
            for job in jobs:
                key = f"{job.symbol}:{job.timeframe}"
                future = executor.submit(
                    self.fetch_single,
                    job.symbol,
                    job.timeframe,
                    job.start_ms,
                    job.end_ms,
                    job.exchange,
                )
                future_to_key[future] = key

            for future in as_completed(future_to_key):
                key = future_to_key[future]
                completed += 1
                try:
                    bars = future.result()
                    results[key] = bars
                    log.debug(
                        "parallel_fetcher.job_done",
                        key=key,
                        bars=len(bars),
                        progress=f"{completed}/{total}",
                    )
                except Exception as exc:
                    log.error("parallel_fetcher.job_failed", key=key, error=str(exc))
                    raise ParallelFetchError(f"parallel fetch failed for {key}: {exc}") from exc
                if progress_callback:
                    progress_callback(key, completed, total)

        log.info(
            "parallel_fetcher.complete",
            jobs=total,
            succeeded=sum(1 for v in results.values() if v),
            failed=sum(1 for v in results.values() if not v),
        )
        return results

    def fetch_chunked(
        self,
        symbol: str,
        timeframe: str,
        start_ms: int,
        end_ms: int,
        chunk_size_ms: int = 7_200_000,  # 2 hours in ms for 15m bars ≈ 480 bars
        exchange: str = "binance",
    ) -> list[Bar]:
        """Fetch large date range in parallel chunks.

        Default chunk_size_ms=7_200_000 (2h) yields ~480 bars at 15m timeframe,
        safely under Binance's 1500 bar limit.
        """
        if end_ms <= start_ms:
            raise ValueError(f"invalid fetch window: start_ms={start_ms} end_ms={end_ms}")

        # Generate chunk jobs
        jobs: list[FetchJob] = []
        chunk_start = start_ms
        while chunk_start < end_ms:
            chunk_end = min(chunk_start + chunk_size_ms, end_ms)
            jobs.append(
                FetchJob(
                    symbol=symbol,
                    timeframe=timeframe,
                    start_ms=chunk_start,
                    end_ms=chunk_end,
                    exchange=exchange,
                )
            )
            chunk_start = chunk_end

        log.info(
            "parallel_fetcher.chunked",
            symbol=symbol,
            timeframe=timeframe,
            chunks=len(jobs),
            range_ms=f"{start_ms}-{end_ms}",
        )

        results = self.fetch_many(jobs)

        # Merge and deduplicate bars by timestamp
        all_bars: list[Bar] = []
        seen_times: set[int] = set()
        for key, bars in results.items():
            for bar in bars:
                if bar.time not in seen_times:
                    seen_times.add(bar.time)
                    all_bars.append(bar)

        # Sort by timestamp
        all_bars.sort(key=lambda b: b.time)
        return all_bars


__all__ = [
    "FetchJob",
    "ParallelFetchError",
    "ParallelDataFetcher",
    "_default_workers",
]
