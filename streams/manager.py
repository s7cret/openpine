"""MarketDataStreamManager and StreamSubscription.

Section 7.9 + 30.3 of OpenPine TZ v3.

Manages live market data subscriptions.
Coordinates with marketdata-provider for live websocket feeds via LiveDataFeedAdapter.

Rules:
- OpenPine does NOT create its own Binance/Bybit websocket layer in bypass of marketdata-provider.
- open/in-progress updates may be stored separately but do not trigger strategy processing by default.
- closed-bar processing is idempotent by key: strategy_id + instrument_key + timeframe + bar_open_time.
- A duplicate KlineUpdate for an already-closed bar must NOT create a duplicate LIVE_BAR_PROCESS.
"""

from __future__ import annotations

import structlog
import uuid
from dataclasses import dataclass, field
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from openpine.contracts import InstrumentKey, Timeframe
    from openpine.events.bus import EventBus
    from openpine.data.data_orchestrator import DataOrchestrator

log = structlog.get_logger(__name__)


class SubscriptionStatus(StrEnum):
    """Stream subscription status."""

    ACTIVE = "active"
    PAUSED = "paused"
    STOPPED = "stopped"


@dataclass
class StreamSubscription:
    """A live market data subscription — section 30.3.

    Attributes:
        subscription_id: Unique subscription identifier.
        instrument_key: Subscribed instrument.
        timeframe: Subscribed timeframe.
        status: Current subscription status.
        provider: Name of the marketdata-provider.
    """

    subscription_id: str
    instrument_key: dict  # serialized InstrumentKey
    timeframe: dict  # serialized Timeframe
    status: SubscriptionStatus = SubscriptionStatus.ACTIVE
    provider: str = "marketdata-provider"

    @classmethod
    def create(
        cls,
        instrument_key: "InstrumentKey",
        timeframe: "Timeframe",
        provider: str = "marketdata-provider",
    ) -> "StreamSubscription":
        """Factory: create a new subscription from domain objects."""
        return cls(
            subscription_id=f"sub_{uuid.uuid4().hex[:16]}",
            instrument_key=instrument_key.model_dump() if hasattr(instrument_key, "model_dump") else dict(instrument_key),
            timeframe=timeframe.model_dump() if hasattr(timeframe, "model_dump") else dict(timeframe),
            status=SubscriptionStatus.ACTIVE,
            provider=provider,
        )


class MarketDataStreamManager:
    """Section 7.9 + 30.3: manages live market data subscriptions.

    Coordinates with marketdata-provider for live websocket feeds.
    Uses LiveDataFeedAdapter for the actual transport.

    Attributes:
        event_bus: EventBus for emitting CandleClosed events.
        data_orchestrator: DataOrchestrator for persisting closed candles.
        _subscriptions: Map of subscription_id -> StreamSubscription.
        _adapter: LiveDataFeedAdapter for the live transport.
    """

    def __init__(
        self,
        event_bus: "EventBus",
        data_orchestrator: "DataOrchestrator",
    ) -> None:
        """Initialize the stream manager.

        Args:
            event_bus: EventBus for event emission.
            data_orchestrator: DataOrchestrator for candle persistence.
        """
        self.event_bus = event_bus
        self.data_orchestrator = data_orchestrator
        self._subscriptions: dict[str, StreamSubscription] = {}
        self._adapter: "LiveDataFeedAdapter | None" = None
        log.info("market_data_stream_manager.init")

    def set_adapter(self, adapter: "LiveDataFeedAdapter | None") -> None:
        """Set the LiveDataFeedAdapter for live transport.

        Args:
            adapter: LiveDataFeedAdapter instance, or None to disconnect.
        """
        self._adapter = adapter
        log.info("market_data_stream_manager.adapter_set", adapter=type(adapter).__name__ if adapter else None)

    def subscribe(
        self,
        instrument_key: "InstrumentKey",
        timeframe: "Timeframe",
    ) -> StreamSubscription:
        """Start streaming bars for instrument/timeframe.

        Uses marketdata-provider if available via LiveDataFeedAdapter.

        Args:
            instrument_key: Instrument to subscribe to.
            timeframe: Timeframe to subscribe to.

        Returns:
            StreamSubscription for the new subscription.
        """
        # Check for existing active subscription
        for sub in self._subscriptions.values():
            if sub.status == SubscriptionStatus.ACTIVE:
                sub_ik = sub.instrument_key
                sub_tf = sub.timeframe
                if sub_ik == (instrument_key.model_dump() if hasattr(instrument_key, "model_dump") else dict(instrument_key)):
                    if sub_tf == (timeframe.model_dump() if hasattr(timeframe, "model_dump") else dict(timeframe)):
                        log.debug("market_data_stream_manager.already_subscribed", subscription_id=sub.subscription_id)
                        return sub

        # Create new subscription
        sub = StreamSubscription.create(instrument_key, timeframe)
        self._subscriptions[sub.subscription_id] = sub

        # Register with adapter if available
        if self._adapter is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                loop.create_task(self._adapter.subscribe(instrument_key, timeframe))
            except RuntimeError:
                # No running loop — adapter must be connected externally
                pass

        log.info(
            "market_data_stream_manager.subscribe",
            subscription_id=sub.subscription_id,
            instrument_key=str(instrument_key),
            timeframe=str(timeframe),
        )
        return sub

    def unsubscribe(self, subscription_id: str) -> None:
        """Stop streaming for a subscription.

        Args:
            subscription_id: ID of the subscription to stop.
        """
        sub = self._subscriptions.get(subscription_id)
        if sub is None:
            log.warning("market_data_stream_manager.unsubscribe.not_found", subscription_id=subscription_id)
            return

        sub.status = SubscriptionStatus.STOPPED

        # Unregister from adapter if available
        if self._adapter is not None:
            import asyncio
            try:
                loop = asyncio.get_running_loop()
                # Reconstruct minimal key objects for unsubscribe
                from openpine.contracts import InstrumentKey as CK, Timeframe as TF
                ik = CK(**sub.instrument_key) if sub.instrument_key else None
                tf = TF(**sub.timeframe) if sub.timeframe else None
                if ik and tf:
                    loop.create_task(self._adapter.unsubscribe(ik, tf))
            except RuntimeError:
                pass

        log.info("market_data_stream_manager.unsubscribe", subscription_id=subscription_id)

    def list_subscriptions(self) -> list[StreamSubscription]:
        """List all subscriptions.

        Returns:
            List of StreamSubscription objects.
        """
        return list(self._subscriptions.values())

    def get_subscription(self, subscription_id: str) -> StreamSubscription | None:
        """Get a subscription by ID.

        Args:
            subscription_id: Subscription ID to look up.

        Returns:
            StreamSubscription or None if not found.
        """
        return self._subscriptions.get(subscription_id)

    def pause(self, subscription_id: str) -> None:
        """Pause a subscription.

        Args:
            subscription_id: ID of the subscription to pause.
        """
        sub = self._subscriptions.get(subscription_id)
        if sub is None:
            log.warning("market_data_stream_manager.pause.not_found", subscription_id=subscription_id)
            return
        sub.status = SubscriptionStatus.PAUSED
        log.info("market_data_stream_manager.pause", subscription_id=subscription_id)

    def resume(self, subscription_id: str) -> None:
        """Resume a paused subscription.

        Args:
            subscription_id: ID of the subscription to resume.
        """
        sub = self._subscriptions.get(subscription_id)
        if sub is None:
            log.warning("market_data_stream_manager.resume.not_found", subscription_id=subscription_id)
            return
        sub.status = SubscriptionStatus.ACTIVE
        log.info("market_data_stream_manager.resume", subscription_id=subscription_id)
