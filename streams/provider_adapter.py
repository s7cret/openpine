"""Live stream adapter boundary for the canonical marketdata-provider package."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any, Callable, Iterable

from marketdata_provider import create_live_kline_client
from marketdata_provider.config import MarketDataConfig
from marketdata_provider.contracts import Bar, InstrumentKey, Timeframe, parse_timeframe
from openpine.data.provider_adapter import ensure_marketdata_provider_version
from openpine.streams.adapter import KlineUpdateEnvelope


def _attr_or_item(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"missing any of: {', '.join(names)}")


def _has_field(obj: Any, name: str) -> bool:
    return (isinstance(obj, dict) and name in obj) or hasattr(obj, name)


def _has_any_field(obj: Any, names: tuple[str, ...]) -> bool:
    return any(_has_field(obj, name) for name in names)


def normalize_provider_kline_update(
    update: Any,
    *,
    instrument_key: InstrumentKey,
    timeframe: Timeframe,
) -> KlineUpdateEnvelope:
    """Normalize supported marketdata-provider kline update shapes."""

    return KlineUpdateEnvelope(
        instrument_key=InstrumentKey(
            exchange=str(_attr_or_item(update, "exchange")).lower()
            if _has_field(update, "exchange")
            else instrument_key.exchange,
            market=str(_attr_or_item(update, "market")).lower()
            if _has_field(update, "market")
            else instrument_key.market,
            symbol=str(_attr_or_item(update, "symbol")).upper()
            if _has_field(update, "symbol")
            else instrument_key.symbol,
        ),
        timeframe=parse_timeframe(str(_attr_or_item(update, "timeframe"))) if _has_field(update, "timeframe") else timeframe,
        timestamp=int(_attr_or_item(update, "open_time", "time", "timestamp", "open_time_ms")),
        open=float(_attr_or_item(update, "open")),
        high=float(_attr_or_item(update, "high")),
        low=float(_attr_or_item(update, "low")),
        close=float(_attr_or_item(update, "close")),
        volume=float(_attr_or_item(update, "volume")) if _has_field(update, "volume") else 0.0,
        closed=bool(_attr_or_item(update, "is_closed", "closed")) if _has_any_field(update, ("is_closed", "closed")) else False,
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
    time_close = time + timeframe.duration_ms if timeframe.duration_ms is not None else time
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
            MarketDataConfig(default_exchange=instrument_key.exchange, default_market=instrument_key.market),
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
