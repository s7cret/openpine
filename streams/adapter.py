"""LiveDataFeedAdapter Protocol and KlineUpdateEnvelope.

Section 30.9 of OpenPine TZ v3.

Protocol for live data feed adapters.
OpenPine live stream boundary with marketdata-provider.

Flow:
    marketdata-provider websocket
    → provider KlineUpdate
    → LiveDataFeedAdapter normalizes to KlineUpdateEnvelope
    → DataOrchestrator persists closed candle atomically
    → EventBus emits CandleClosed
    → JobScheduler enqueues LIVE_BAR_PROCESS/PAPER_BAR_PROCESS

Rules:
- OpenPine does NOT create its own Binance/Bybit websocket layer in bypass of marketdata-provider.
- open/in-progress updates may be stored separately but do not trigger strategy processing.
- closed-bar processing is idempotent by key: strategy_id + instrument_key + timeframe + bar_open_time.
- A duplicate KlineUpdate for an already-closed bar must NOT create duplicate LIVE_BAR_PROCESS.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Callable, Protocol

if TYPE_CHECKING:
    from openpine.contracts import Bar, InstrumentKey, Timeframe


class KlineUpdateEnvelope:
    """Wrapper for kline/candlestick updates from exchange websocket.

    Section 30.3: normalized form from marketdata-provider.

    Attributes:
        instrument_key: Instrument key for the update.
        timeframe: Timeframe for the update.
        timestamp: Update timestamp in milliseconds.
        open: Opening price.
        high: High price.
        low: Low price.
        close: Closing price.
        volume: Trading volume.
        closed: Whether the bar is confirmed closed.
    """

    def __init__(
        self,
        instrument_key: "InstrumentKey | dict",
        timeframe: "Timeframe | dict",
        timestamp: int,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float,
        closed: bool,
    ) -> None:
        self.instrument_key = instrument_key
        self.timeframe = timeframe
        self.timestamp = timestamp
        self.open = open
        self.high = high
        self.low = low
        self.close = close
        self.volume = volume
        self.closed = closed

    def __repr__(self) -> str:
        return (
            f"KlineUpdateEnvelope(closed={self.closed} "
            f"close={self.close} vol={self.volume})"
        )


class LiveDataFeedAdapter(Protocol):
    """Section 30.9: Protocol for live data feed adapters.

    OpenPine live stream boundary with marketdata-provider.
    OpenPine does NOT implement its own exchange websocket clients.

    Implementors:
        - StubLiveDataFeedAdapter: placeholder when marketdata-provider is unavailable.
        - ProviderLiveDataFeedAdapter: real adapter bridging marketdata-provider.

    Required methods:
        connect: Establish connection to the feed.
        disconnect: Close the connection.
        subscribe: Start receiving updates for instrument/timeframe.
        unsubscribe: Stop receiving updates for instrument/timeframe.
        on_bar: Register a callback for closed bar events.
    """

    async def connect(self) -> None:
        """Establish connection to the live data feed."""
        ...

    async def disconnect(self) -> None:
        """Close the connection to the live data feed."""
        ...

    async def subscribe(
        self,
        instrument_key: "InstrumentKey",
        timeframe: "Timeframe",
    ) -> None:
        """Start streaming for instrument/timeframe.

        Args:
            instrument_key: Instrument to subscribe to.
            timeframe: Timeframe to subscribe to.
        """
        ...

    async def unsubscribe(
        self,
        instrument_key: "InstrumentKey",
        timeframe: "Timeframe",
    ) -> None:
        """Stop streaming for instrument/timeframe.

        Args:
            instrument_key: Instrument to unsubscribe from.
            timeframe: Timeframe to unsubscribe from.
        """
        ...

    def on_bar(self, callback: Callable[["Bar"], None]) -> None:
        """Register a callback for confirmed closed bars.

        The callback is invoked for each confirmed closed bar received
        from the live feed. The caller is responsible for calling
        DataOrchestrator.on_candle_closed and then EventBus.emit_candle_closed
        (section 33.1).

        Args:
            callback: Callable that receives the closed Bar.
        """
        ...
