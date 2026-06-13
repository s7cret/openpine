"""Direct HTTP Binance provider — bypasses marketdata_provider hangs."""

from __future__ import annotations

import math
import json as _json
import urllib.request
import urllib.error

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
)
from openpine.data.persistent_cache import (
    cache_enabled_by_env,
    default_cache_dir,
    load_bar_series,
    save_bar_series,
)

BINANCE_PAGE_LIMIT = 1000
_EARLIEST_OPEN_CACHE: dict[tuple[str, str, str], int | None] = {}


class DirectBinanceProvider:
    """Simple HTTP provider with listing-aware ranges and persistent cache."""

    def get_earliest_open_time(self, query: BarQuery) -> int | None:
        """Return the first known Binance kline open time for this instrument."""
        interval, symbol, base, _ = self._binance_context(query)
        cache_key = (base, symbol, interval)
        if cache_key in _EARLIEST_OPEN_CACHE:
            return _EARLIEST_OPEN_CACHE[cache_key]

        url = f"{base}?symbol={symbol}&interval={interval}&startTime=0&endTime={query.end_ms}&limit=1"
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "OpenPine/1.0"})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw = _json.loads(resp.read().decode())
        except (urllib.error.URLError, TimeoutError, OSError, ValueError):
            _EARLIEST_OPEN_CACHE[cache_key] = None
            return None

        earliest = int(raw[0][0]) if raw else None
        _EARLIEST_OPEN_CACHE[cache_key] = earliest
        return earliest

    def effective_query(self, query: BarQuery) -> tuple[BarQuery, int | None]:
        """Clamp requested start to the instrument listing start when known."""
        earliest = self.get_earliest_open_time(query)
        effective_start = (
            max(query.start_ms, earliest) if earliest is not None else query.start_ms
        )
        return self._copy_query(query, start_ms=effective_start), earliest

    @staticmethod
    def estimate_bars(
        query: BarQuery, *, start_ms: int | None = None, end_ms: int | None = None
    ) -> int:
        duration_ms = query.timeframe.duration_ms or 60000
        start = query.start_ms if start_ms is None else start_ms
        end = query.end_ms if end_ms is None else end_ms
        if end < start:
            return 0
        return (end - start) // duration_ms + 1

    @classmethod
    def estimate_pages(
        cls, query: BarQuery, *, start_ms: int | None = None, end_ms: int | None = None
    ) -> int:
        return math.ceil(
            cls.estimate_bars(query, start_ms=start_ms, end_ms=end_ms)
            / BINANCE_PAGE_LIMIT
        )

    def fetch_bars(self, query: BarQuery, progress_callback=None) -> BarSeries:
        interval, symbol, base, duration_ms = self._binance_context(query)
        effective_query, earliest_open_ms = self.effective_query(query)
        total_bars = self.estimate_bars(effective_query)
        total_pages = self.estimate_pages(effective_query)

        if progress_callback:
            progress_callback(
                0, 0, total_bars, total_pages, earliest_open_ms, "cache_lookup"
            )

        if cache_enabled_by_env():
            cached = load_bar_series(default_cache_dir(), effective_query)
            if cached is not None:
                if progress_callback:
                    progress_callback(
                        len(cached.bars),
                        total_pages,
                        total_bars,
                        total_pages,
                        earliest_open_ms,
                        "cache_hit",
                    )
                return cached

        all_bars: list[Bar] = []
        cursor = effective_query.start_ms
        max_pages = max(total_pages + 5, 1)

        instrument = InstrumentKey(
            exchange=query.instrument.exchange,
            market=query.instrument.market,
            symbol=symbol,
        )

        for page_num in range(max_pages):
            url = f"{base}?symbol={symbol}&interval={interval}&startTime={cursor}&endTime={effective_query.end_ms}&limit={BINANCE_PAGE_LIMIT}"
            try:
                req = urllib.request.Request(
                    url, headers={"User-Agent": "OpenPine/1.0"}
                )
                with urllib.request.urlopen(req, timeout=15) as resp:
                    raw = _json.loads(resp.read().decode())
            except (urllib.error.URLError, TimeoutError, OSError):
                break

            if not raw:
                break

            batch_bars: list[Bar] = []
            for k in raw:
                open_time = int(k[0])
                close_time = open_time + duration_ms
                batch_bars.append(
                    Bar(
                        instrument=instrument,
                        timeframe=query.timeframe,
                        time=open_time,
                        time_close=close_time,
                        open=float(k[1]),
                        high=float(k[2]),
                        low=float(k[3]),
                        close=float(k[4]),
                        volume=float(k[5]),
                        closed=True,
                    )
                )

            all_bars.extend(batch_bars)

            # Report progress every 10 pages
            if progress_callback and (page_num + 1) % 10 == 0:
                progress_callback(
                    len(all_bars),
                    page_num + 1,
                    total_bars,
                    total_pages,
                    earliest_open_ms,
                    "fetch",
                )

            if len(raw) < BINANCE_PAGE_LIMIT:
                break
            # Advance cursor past the last bar
            cursor = int(raw[-1][0]) + duration_ms
            if cursor >= effective_query.end_ms:
                break

        # Final progress callback
        if progress_callback:
            fetched_pages = min(
                math.ceil(len(all_bars) / BINANCE_PAGE_LIMIT), total_pages
            )
            progress_callback(
                len(all_bars),
                fetched_pages,
                total_bars,
                total_pages,
                earliest_open_ms,
                "fetch_done",
            )

        # Sort and deduplicate
        by_time = {b.time: b for b in all_bars}
        sorted_bars = tuple(by_time[t] for t in sorted(by_time))

        coverage = CoverageReport(
            requested_start_ms=effective_query.start_ms,
            requested_end_ms=effective_query.end_ms,
            delivered_start_ms=sorted_bars[0].time if sorted_bars else None,
            delivered_end_ms=sorted_bars[-1].time_close if sorted_bars else None,
            missing_intervals=(),
            duplicate_timestamps=(),
            source_mix=("provider",),
            status="valid" if sorted_bars else "empty",
        )
        series = BarSeries(query=effective_query, bars=sorted_bars, coverage=coverage)
        if cache_enabled_by_env() and sorted_bars:
            save_bar_series(default_cache_dir(), series)
        return series

    @staticmethod
    def _copy_query(query: BarQuery, *, start_ms: int) -> BarQuery:
        return BarQuery(
            instrument=query.instrument,
            timeframe=query.timeframe,
            start_ms=start_ms,
            end_ms=query.end_ms,
            source=query.source,
            gap_policy=query.gap_policy,
            error_policy=query.error_policy,
        )

    @staticmethod
    def _binance_context(query: BarQuery) -> tuple[str, str, str, int]:
        interval = query.timeframe.canonical.lower()
        symbol = query.instrument.symbol.upper()
        is_futures = query.instrument.market in ("futures", "delivery")
        base = (
            "https://fapi.binance.com/fapi/v1/klines"
            if is_futures
            else "https://api.binance.com/api/v3/klines"
        )
        duration_ms = query.timeframe.duration_ms or 60000
        return interval, symbol, base, duration_ms
