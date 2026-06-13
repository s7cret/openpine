"""openpine.streams — live market data streaming.

Section 7.9 + 30.3 + 30.9 of OpenPine TZ v3.

Exports:
    MarketDataStreamManager: manages live market data subscriptions.
    StreamSubscription: a live subscription object.
    LiveDataFeedAdapter: Protocol for live feed adapters.
    KlineUpdateEnvelope: wrapper for exchange kline updates.
    SubscriptionStatus: subscription state enum.
"""

from openpine.streams.adapter import (
    KlineUpdateEnvelope,
    LiveDataFeedAdapter,
)
from openpine.streams.provider_adapter import (
    LocalProviderLiveDataFeedAdapter,
    create_local_live_data_feed_adapter,
    envelope_to_bar,
    normalize_provider_kline_update,
)
from openpine.streams.manager import (
    MarketDataStreamManager,
    StreamSubscription,
    SubscriptionStatus,
)

__all__ = [
    "MarketDataStreamManager",
    "StreamSubscription",
    "LiveDataFeedAdapter",
    "KlineUpdateEnvelope",
    "SubscriptionStatus",
    "LocalProviderLiveDataFeedAdapter",
    "create_local_live_data_feed_adapter",
    "envelope_to_bar",
    "normalize_provider_kline_update",
]
