from __future__ import annotations

import asyncio
from types import SimpleNamespace

from marketdata_provider.contracts import InstrumentKey, parse_timeframe

from openpine.streams import provider_adapter


class _FakeLiveClient:
    async def events(self, *, max_messages=None, timeout_s=None):
        del max_messages, timeout_s
        yield SimpleNamespace(
            update=SimpleNamespace(
                exchange="binance",
                market="spot",
                symbol="BTCUSDT",
                timeframe="1m",
                open_time=60_000,
                open=1.0,
                high=2.0,
                low=0.5,
                close=1.5,
                volume=3.0,
                is_closed=True,
            )
        )
        await asyncio.sleep(60)


def test_live_adapter_uses_stable_marketdata_factory(monkeypatch) -> None:
    created = []

    def fake_factory(config, *, instrument, timeframe):
        created.append((config, instrument, timeframe))
        return _FakeLiveClient()

    monkeypatch.setattr(provider_adapter, "create_live_kline_client", fake_factory)
    received = []
    adapter = provider_adapter.LocalProviderLiveDataFeedAdapter()
    adapter.on_bar(received.append)

    async def run() -> None:
        await adapter.subscribe(
            InstrumentKey("binance", "spot", "BTCUSDT"),
            parse_timeframe("1m"),
        )
        for _ in range(20):
            if received:
                break
            await asyncio.sleep(0.01)
        await adapter.disconnect()

    asyncio.run(run())

    assert created
    assert received[0].time == 60_000
    assert received[0].instrument.symbol == "BTCUSDT"
