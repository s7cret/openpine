"""Direct Binance DataProvider for pinelib request.security support.

Simple HTTP fetcher with 10s timeout. Returns pinelib Bar objects
compatible with the DataProvider protocol expected by request.security.
"""
from __future__ import annotations

import json as _json
import urllib.request
import urllib.error

from pinelib.core.bar import Bar as PinelibBar


# Canonical Binance interval mapping
_INTERVAL_MAP = {
    "1": "1m", "3": "3m", "5": "5m", "15": "15m", "30": "30m",
    "60": "1h", "120": "2h", "240": "4h", "360": "6h", "480": "8h",
    "720": "12h", "D": "1d", "1D": "1d", "W": "1w", "1W": "1w",
    "M": "1M", "1M": "1M",
}


def _to_binance_interval(timeframe: str) -> str:
    """Convert pinelib timeframe string to Binance kline interval."""
    tf = timeframe.strip().upper()
    if tf in _INTERVAL_MAP:
        return _INTERVAL_MAP[tf]
    # Already a Binance interval like "1m", "15m", "1h"
    if tf.endswith(("S", "M", "H", "D", "W")):
        return timeframe.lower()
    return timeframe.lower()


def _interval_to_ms(interval: str) -> int:
    """Convert Binance interval to milliseconds."""
    unit = interval[-1]
    val = int(interval[:-1])
    multipliers = {"s": 1000, "m": 60_000, "h": 3_600_000, "d": 86_400_000, "w": 604_800_000}
    return val * multipliers.get(unit, 60_000)


class DirectBinanceDataProvider:
    """DataProvider that fetches bars directly from Binance via HTTP.

    Implements the pinelib DataProvider protocol:
        get_bars(symbol, timeframe, start, end, max_bars=) -> list[pinelib.Bar]
    """

    def __init__(self, *, market: str = "spot", timeout: int = 10) -> None:
        self.market = market.lower()
        self.timeout = timeout
        self._base = (
            "https://fapi.binance.com/fapi/v1/klines"
            if self.market in ("futures", "delivery")
            else "https://api.binance.com/api/v3/klines"
        )

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: int | None,
        end: int | None,
        *,
        max_bars: int | None = None,
    ) -> list[PinelibBar]:
        if start is None or end is None:
            return []

        interval = _to_binance_interval(timeframe)
        duration_ms = _interval_to_ms(interval)
        symbol_upper = symbol.upper()

        all_bars: list[PinelibBar] = []
        cursor = start
        max_pages = 20  # safety limit

        for _ in range(max_pages):
            url = (
                f"{self._base}?symbol={symbol_upper}"
                f"&interval={interval}&startTime={cursor}&endTime={end}&limit=1000"
            )
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "OpenPine/1.0"})
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = _json.loads(resp.read().decode())
            except (urllib.error.URLError, TimeoutError, OSError):
                break

            if not raw:
                break

            for k in raw:
                open_time = int(k[0])
                close_time = open_time + duration_ms
                all_bars.append(PinelibBar(
                    time=open_time,
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    time_close=close_time,
                ))

            if len(raw) < 1000:
                break
            cursor = int(raw[-1][0]) + duration_ms
            if cursor >= end:
                break

        # Deduplicate by time
        by_time = {b.time: b for b in all_bars}
        sorted_bars = [by_time[t] for t in sorted(by_time)]

        if max_bars is not None:
            sorted_bars = sorted_bars[:max_bars]

        return sorted_bars
