from __future__ import annotations

from marketdata_provider.contracts import InstrumentKey, parse_timeframe

from openpine.streams.manager import MarketDataStreamManager, SubscriptionStatus


def _manager() -> MarketDataStreamManager:
    return MarketDataStreamManager(event_bus=object(), data_orchestrator=object())


def test_stream_manager_reuses_active_subscription_for_same_market() -> None:
    manager = _manager()
    instrument = InstrumentKey("binance", "spot", "BTCUSDT")
    timeframe = parse_timeframe("1m")

    first = manager.subscribe(instrument, timeframe)
    second = manager.subscribe(instrument, timeframe)

    assert second is first
    assert manager.list_subscriptions() == [first]


def test_stream_manager_lifecycle_status_transitions() -> None:
    manager = _manager()
    sub = manager.subscribe(
        InstrumentKey("binance", "spot", "BTCUSDT"),
        parse_timeframe("1m"),
    )

    manager.pause(sub.subscription_id)
    assert manager.get_subscription(sub.subscription_id).status == SubscriptionStatus.PAUSED

    manager.resume(sub.subscription_id)
    assert manager.get_subscription(sub.subscription_id).status == SubscriptionStatus.ACTIVE

    manager.unsubscribe(sub.subscription_id)
    assert manager.get_subscription(sub.subscription_id).status == SubscriptionStatus.STOPPED
