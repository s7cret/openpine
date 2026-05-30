"""BarQuery dataclass for candle storage queries.

Section OP-DL-004 of OpenPine.
Section 7.5 of OpenPine TZ v3.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional


@dataclass(frozen=True)
class BarQuery:
    """Query for reading bars from candle storage.

    Attributes:
        instrument_key: Full instrument key (e.g. "binance:spot:BTCUSDT:trade")
        timeframe: Timeframe string (e.g. "1m", "5m", "15m")
        from_time: Start time in UTC epoch ms (inclusive), None = earliest
        to_time: End time in UTC epoch ms (inclusive), None = latest
        limit: Max number of bars to return, None = no limit
        include_open_candle: Whether to include the currently open candle
        source: Where to fetch from — "storage" (parquet only), "provider" (live only),
                "auto" (try storage first, then provider)
    """

    instrument_key: str
    timeframe: str
    from_time: Optional[int] = None
    to_time: Optional[int] = None
    limit: Optional[int] = None
    include_open_candle: bool = False
    source: Literal["storage", "provider", "auto"] = "auto"

    def __post_init__(self) -> None:
        """Validate query parameters."""
        if self.from_time is not None and self.to_time is not None:
            if self.from_time > self.to_time:
                raise ValueError(f"from_time ({self.from_time}) must be <= to_time ({self.to_time})")

    @property
    def instrument_parts(self) -> tuple[str, str, str, str]:
        """Parse instrument_key into (exchange, market_type, symbol, price_type)."""
        parts = self.instrument_key.split(":")
        if len(parts) != 4:
            raise ValueError(f"Invalid instrument_key format: {self.instrument_key}")
        return (parts[0], parts[1], parts[2], parts[3])


__all__ = ["BarQuery"]
