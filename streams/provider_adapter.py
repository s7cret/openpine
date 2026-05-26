"""Live stream adapter boundary for local marketdata-provider installations."""

from __future__ import annotations

import importlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Iterable

from openpine.contracts import Bar, InstrumentKey, Timeframe
from openpine.data.provider_adapter import (
    DEFAULT_PROVIDER_ROOTS,
    LocalProviderInstallation,
    detect_local_marketdata_provider,
    ensure_provider_import_path,
)
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
            symbol=str(_attr_or_item(update, "symbol")) if _has_field(update, "symbol") else instrument_key.symbol,
            exchange=str(_attr_or_item(update, "exchange")).upper() if _has_field(update, "exchange") else instrument_key.exchange,
            base=instrument_key.base,
            quote=instrument_key.quote,
        ),
        timeframe=Timeframe(
            value=str(_attr_or_item(update, "timeframe")) if _has_field(update, "timeframe") else timeframe.value,
        ),
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
    return Bar(
        instrument_key=instrument,
        timeframe=timeframe,
        timestamp=envelope.timestamp,
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

    installation: LocalProviderInstallation
    _callbacks: list[Callable[[Bar], None]] = field(default_factory=list)
    _clients: dict[tuple[str, str], Any] = field(default_factory=dict)

    @classmethod
    def from_local_installation(
        cls,
        installation: LocalProviderInstallation | None = None,
    ) -> "LocalProviderLiveDataFeedAdapter | None":
        installation = installation or detect_local_marketdata_provider()
        if installation is None:
            return None
        return cls(installation)

    async def connect(self) -> None:
        ensure_provider_import_path(self.installation)

    async def disconnect(self) -> None:
        for client in list(self._clients.values()):
            stop = getattr(client, "stop", None)
            if stop is not None:
                stop()
        self._clients.clear()

    async def subscribe(
        self,
        instrument_key: InstrumentKey,
        timeframe: Timeframe,
    ) -> None:
        ensure_provider_import_path(self.installation)
        stream_module = importlib.import_module("marketdata_provider.streaming")
        symbol = instrument_key.symbol
        tf = timeframe.value
        exchange = instrument_key.exchange.lower()

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

        if exchange == "bybit":
            client_cls = getattr(stream_module, "BybitWebSocket", None)
            if client_cls is None:
                client_cls = getattr(importlib.import_module("marketdata_provider.streaming.ws_client"), "BybitWebSocket")
            client = client_cls(symbol, tf, on_kline=on_kline)
        else:
            client_cls = getattr(stream_module, "BinanceWebSocket", None)
            if client_cls is None:
                client_cls = getattr(importlib.import_module("marketdata_provider.streaming.ws_client"), "BinanceWebSocket")
            client = client_cls(symbol, tf, on_kline=on_kline)

        self._clients[(str(instrument_key), tf)] = client
        start = getattr(client, "start", None)
        if start is not None:
            start()

    async def unsubscribe(
        self,
        instrument_key: InstrumentKey,
        timeframe: Timeframe,
    ) -> None:
        client = self._clients.pop((str(instrument_key), timeframe.value), None)
        stop = getattr(client, "stop", None)
        if stop is not None:
            stop()

    def on_bar(self, callback: Callable[[Bar], None]) -> None:
        self._callbacks.append(callback)


def create_local_live_data_feed_adapter(
    roots: Iterable[str | Path] = DEFAULT_PROVIDER_ROOTS,
) -> LocalProviderLiveDataFeedAdapter | None:
    """Create a local live-feed adapter, or None when no local package exists."""

    installation = detect_local_marketdata_provider(roots)
    if installation is None:
        return None
    return LocalProviderLiveDataFeedAdapter.from_local_installation(installation)


__all__ = [
    "LocalProviderLiveDataFeedAdapter",
    "create_local_live_data_feed_adapter",
    "envelope_to_bar",
    "normalize_provider_kline_update",
]
