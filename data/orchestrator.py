"""DataOrchestrator — single boundary for bar reading/writing.

Section 7.5 + 33.1 of OpenPine TZ v3.

This is the ONLY OpenPine layer allowed to:
- Read historical bars (via get_bars)
- Write confirmed closed candles from live feeds (via on_candle_closed)

Rules (section 33.1):
- DataOrchestrator.get_bars(query: BarQuery) -> list[Bar] is the ONLY read contract for bars.
- DataOrchestrator.on_candle_closed(bar, instrument_key, timeframe, source) is the ONLY write boundary
  for confirmed closed candles from live feeds.
- EventBus.emit(CandleClosed) happens only after on_candle_closed durable write succeeds.
"""

from __future__ import annotations

import structlog
from typing import Optional, Protocol, runtime_checkable

from openpine.contracts import Bar as ContractBar
from openpine.contracts import BarQuery as ContractBarQuery

log = structlog.get_logger(__name__)


@runtime_checkable
class MarketDataProvider(Protocol):
    """Protocol for market data providers that can supply bars.

    Market data provider must implement get_bars method.
    If unavailable, should raise or return empty list gracefully.
    """

    def get_bars(self, query: ContractBarQuery) -> list[ContractBar]:
        """Get bars for the given query."""
        ...


class DataOrchestrator:
    """Section 7.5 + 33.1: ONLY contract for bar reading/writing.

    This class is the single OpenPine boundary for:
    - Historical bar reads (get_bars)
    - Live candle persistence (on_candle_closed)

    All bar reading goes through get_bars().
    All closed candle writing goes through on_candle_closed().
    """

    def __init__(self) -> None:
        """Initialize the DataOrchestrator."""
        self._closed_bars: list[ContractBar] = []
        self._provider: Optional[MarketDataProvider] = None
        self._persist_count: int = 0

    def set_provider(self, provider: MarketDataProvider) -> None:
        """Set the market data provider for bar fetching.

        Args:
            provider: A MarketDataProvider implementation.
        """
        self._provider = provider

    def get_bars(self, query: ContractBarQuery) -> list[ContractBar]:
        """Single read contract for bars (section 33.1).

        Uses marketdata-provider if available.
        Returns [] if provider is unavailable or returns nothing.

        Args:
            query: BarQuery specifying instrument, timeframe, time range.

        Returns:
            List of Bar objects matching the query, or empty list if unavailable.
        """
        # Try provider first if available
        if self._provider is not None:
            try:
                bars = self._provider.get_bars(query)
                log.debug("data_orchestrator.get_bars", query=str(query), count=len(bars))
                return bars
            except Exception as e:
                log.warning("data_orchestrator.provider_error", query=str(query), error=str(e))
                return []

        # No provider available
        log.debug("data_orchestrator.get_bars.no_provider", query=str(query))
        return []

    def on_candle_closed(
        self,
        bar: ContractBar,
        *,
        instrument_key: str,
        timeframe: str,
        source: str = "live",
    ) -> None:
        """Write boundary for confirmed closed candles from live feeds (section 33.1).

        Stores bar durably before emitting events.
        After this call succeeds, EventBus.emit(CandleClosed) can be called by caller.

        Args:
            bar: The confirmed closed Bar.
            instrument_key: Full instrument key string.
            timeframe: Timeframe string (e.g. "1m", "5m").
            source: Source of the candle ("live", "backfill", "import").
        """
        # Validate unique candle key - use bar's canonical instrument_key and timeframe
        candle_key = f"{str(bar.instrument_key)}:{bar.timeframe.value}:{bar.open_time_ms}"

        # Check for duplicates using string representation of instrument_key
        existing_keys = {f"{str(b.instrument_key)}:{b.timeframe.value}:{b.open_time_ms}" for b in self._closed_bars}
        if candle_key in existing_keys:
            log.debug("data_orchestrator.duplicate_candle_skipped", candle_key=candle_key)
            return

        # Store bar durably (in-memory for now, would persist to storage in full impl)
        self._closed_bars.append(bar)
        self._persist_count += 1

        log.info(
            "data_orchestrator.candle_closed_persisted",
            candle_key=candle_key,
            source=source,
            persist_count=self._persist_count,
        )

    @property
    def closed_bars(self) -> list[ContractBar]:
        """Return all closed bars stored in memory.

        For testing purposes. In production, bars would be persisted to storage.
        """
        return list(self._closed_bars)

    @property
    def persist_count(self) -> int:
        """Return count of persisted bars.

        For testing/monitoring purposes.
        """
        return self._persist_count


__all__ = [
    "DataOrchestrator",
    "MarketDataProvider",
]
