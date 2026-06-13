from __future__ import annotations

import asyncio
from types import SimpleNamespace

from marketdata_provider.contracts import InstrumentKey, parse_timeframe

from openpine.streams.manager import MarketDataStreamManager, StreamSubscription


class Adapter:
    def __init__(self) -> None:
        self.subscribed = []
        self.unsubscribed = []

    async def subscribe(self, instrument_key, timeframe):
        self.subscribed.append((instrument_key, timeframe))

    async def unsubscribe(self, instrument_key, timeframe):
        self.unsubscribed.append((instrument_key, timeframe))


def _key(symbol: str) -> InstrumentKey:
    return InstrumentKey(exchange="binance", market="spot", symbol=symbol)


def test_stream_manager_final_loop_continue_and_unsubscribe_false_branch():
    async def run() -> None:
        manager = MarketDataStreamManager(SimpleNamespace(), SimpleNamespace())
        adapter = Adapter()
        manager.set_adapter(adapter)

        existing = StreamSubscription.create(_key("ETHUSDT"), parse_timeframe("1m"))
        manager._subscriptions[existing.subscription_id] = existing

        # Covers the for-loop branch that checks an active subscription but
        # continues because instrument/timeframe do not match (146 -> 145).
        created = manager.subscribe(_key("BTCUSDT"), parse_timeframe("1m"))
        await asyncio.sleep(0)
        assert created.subscription_id != existing.subscription_id
        assert adapter.subscribed

        # Covers unsubscribe branch with adapter present but missing serialized
        # key/timeframe, so `if ik and tf` is false and execution jumps to log.
        broken = StreamSubscription(
            subscription_id="broken",
            instrument_key={},
            timeframe={},
        )
        manager._subscriptions[broken.subscription_id] = broken
        manager.unsubscribe("broken")
        await asyncio.sleep(0)
        assert adapter.unsubscribed == []

    asyncio.run(run())
