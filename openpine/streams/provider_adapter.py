"""Live stream adapter boundary for the canonical marketdata-provider package."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from openpine.data.row_helpers import attr_or_item, has_any_field, has_field

from marketdata_provider import create_live_kline_client
from marketdata_provider.config import MarketDataConfig
from marketdata_provider.contracts import Bar, InstrumentKey, Timeframe, parse_timeframe
from openpine.data.provider_adapter import ensure_marketdata_provider_version
from openpine.streams.adapter import KlineUpdateEnvelope








def normalize_provider_kline_update(
    update: Any,
    *,
    instrument_key: InstrumentKey,
    timeframe: Timeframe,
) -> KlineUpdateEnvelope:
    """Normalize supported marketdata-provider kline update shapes."""
    provider_bar = getattr(update, "bar", None)
    if provider_bar is not None:
        return normalize_provider_kline_update(
            provider_bar,
            instrument_key=instrument_key,
            timeframe=timeframe,
        )

    if hasattr(update, "instrument") and hasattr(update, "time"):
        provider_instrument = attr_or_item(update, "instrument")
        provider_timeframe = (
            attr_or_item(update, "timeframe")
            if has_field(update, "timeframe")
            else timeframe
        )
        return KlineUpdateEnvelope(
            instrument_key=provider_instrument,
            timeframe=provider_timeframe,
            timestamp=int(attr_or_item(update, "time")),
            open=float(attr_or_item(update, "open")),
            high=float(attr_or_item(update, "high")),
            low=float(attr_or_item(update, "low")),
            close=float(attr_or_item(update, "close")),
            volume=(
                float(attr_or_item(update, "volume"))
                if has_field(update, "volume")
                else 0.0
            ),
            closed=(
                bool(attr_or_item(update, "closed"))
                if has_field(update, "closed")
                else False
            ),
        )

    return KlineUpdateEnvelope(
        instrument_key=InstrumentKey(
            exchange=(
                str(attr_or_item(update, "exchange")).lower()
                if has_field(update, "exchange")
                else instrument_key.exchange
            ),
            market=(
                str(attr_or_item(update, "market")).lower()
                if has_field(update, "market")
                else instrument_key.market
            ),
            symbol=(
                str(attr_or_item(update, "symbol")).upper()
                if has_field(update, "symbol")
                else instrument_key.symbol
            ),
        ),
        timeframe=(
            parse_timeframe(str(attr_or_item(update, "timeframe")))
            if has_field(update, "timeframe")
            else timeframe
        ),
        timestamp=int(
            attr_or_item(update, "open_time", "time", "timestamp", "open_time_ms")
        ),
        open=float(attr_or_item(update, "open")),
        high=float(attr_or_item(update, "high")),
        low=float(attr_or_item(update, "low")),
        close=float(attr_or_item(update, "close")),
        volume=(
            float(attr_or_item(update, "volume"))
            if has_field(update, "volume")
            else 0.0
        ),
        closed=(
            bool(attr_or_item(update, "is_closed", "closed"))
            if has_any_field(update, ("is_closed", "closed"))
            else False
        ),
    )


def envelope_to_bar(envelope: KlineUpdateEnvelope) -> Bar:
    """Convert a normalized kline envelope to OpenPine's Bar contract."""

    instrument = envelope.instrument_key
    timeframe = envelope.timeframe
    if isinstance(instrument, dict):
        instrument = InstrumentKey(**instrument)
    if isinstance(timeframe, dict):
        timeframe = Timeframe(**timeframe)
    time = int(envelope.timestamp)
    time_close = (
        time + timeframe.duration_ms if timeframe.duration_ms is not None else time
    )
    return Bar(
        instrument=instrument,
        timeframe=timeframe,
        time=time,
        time_close=time_close,
        open=envelope.open,
        high=envelope.high,
        low=envelope.low,
        close=envelope.close,
        volume=envelope.volume,
        closed=envelope.closed,
    )


@dataclass
class LocalProviderLiveDataFeedAdapter:
    """OpenPine live-data boundary around marketdata-provider websocket clients."""

    _callbacks: list[Callable[[Bar], None]] = field(default_factory=list)
    _clients: dict[tuple[str, str], Any] = field(default_factory=dict)
    _tasks: dict[tuple[str, str], asyncio.Task[None]] = field(default_factory=dict)

    async def connect(self) -> None:
        ensure_marketdata_provider_version()

    async def disconnect(self) -> None:
        for task in list(self._tasks.values()):
            task.cancel()
        for task in list(self._tasks.values()):
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
        self._clients.clear()

    async def subscribe(
        self,
        instrument_key: InstrumentKey,
        timeframe: Timeframe,
    ) -> None:
        ensure_marketdata_provider_version()

        def on_kline(update: Any) -> None:
            envelope = normalize_provider_kline_update(
                update,
                instrument_key=instrument_key,
                timeframe=timeframe,
            )
            if not envelope.closed:
                return
            bar = envelope_to_bar(envelope)
            for callback in list(self._callbacks):
                callback(bar)

        client = create_live_kline_client(
            MarketDataConfig(
                default_exchange=instrument_key.exchange,
                default_market=instrument_key.market,
            ),
            instrument=instrument_key,
            timeframe=timeframe,
        )
        key = (str(instrument_key), timeframe.canonical)
        self._clients[key] = client

        async def _consume() -> None:
            async for event in client.events():
                update = getattr(event, "update", event)
                on_kline(update)

        self._tasks[key] = asyncio.create_task(_consume())

    async def unsubscribe(
        self,
        instrument_key: InstrumentKey,
        timeframe: Timeframe,
    ) -> None:
        key = (str(instrument_key), timeframe.canonical)
        self._clients.pop(key, None)
        task = self._tasks.pop(key, None)
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    def on_bar(self, callback: Callable[[Bar], None]) -> None:
        self._callbacks.append(callback)


def create_local_live_data_feed_adapter(
    roots: Iterable[str] | None = None,
) -> LocalProviderLiveDataFeedAdapter:
    """Create a local live-feed adapter using the installed canonical package."""

    del roots
    ensure_marketdata_provider_version()
    return LocalProviderLiveDataFeedAdapter()


__all__ = [
    "LocalProviderLiveDataFeedAdapter",
    "create_local_live_data_feed_adapter",
    "envelope_to_bar",
    "normalize_provider_kline_update",
]
