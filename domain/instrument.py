"""Domain types for instruments and timeframes.

Import base types from openpine.contracts to avoid redefinition.
"""

from __future__ import annotations

from typing import Optional

from openpine.contracts import InstrumentKey as ContractInstrumentKey
from openpine.contracts import Timeframe as ContractTimeframe
from openpine.contracts import Bar as ContractBar


class InstrumentKey(ContractInstrumentKey):
    """Extended instrument key with full instrument details.

    Extends the contracts InstrumentKey with domain-specific helpers.
    """

    @property
    def full_key(self) -> str:
        """Return full instrument key string: exchange:market_type:symbol:price_type."""
        market_type = getattr(self, 'market_type', 'spot')
        price_type = getattr(self, 'price_type', 'trade')
        return f"{self.exchange}:{market_type}:{self.symbol}:{price_type}"


class Timeframe(ContractTimeframe):
    """Extended timeframe with domain helpers."""

    @property
    def milliseconds(self) -> int:
        """Return timeframe duration in milliseconds."""
        return self.minutes * 60 * 1000

    @property
    def seconds(self) -> int:
        """Return timeframe duration in seconds."""
        return self.minutes * 60


class Bar(ContractBar):
    """Extended Bar with guaranteed open_time_ms and close_time_ms properties.

    These properties are already defined in the contracts Bar class.
    This class exists for explicit domain extensions if needed.
    """

    pass


__all__ = [
    "InstrumentKey",
    "Timeframe",
    "Bar",
]
