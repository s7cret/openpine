"""OpenPine boundary around the canonical marketdata-provider package."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable

import structlog
from marketdata_provider.config import BinanceConfig, BybitConfig
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
)
from marketdata_provider.exchanges.binance.provider import binance_get_bars_sync
from marketdata_provider.exchanges.bybit.provider import bybit_get_bars_sync
from marketdata_provider.timeframes import close_time_ms

log = structlog.get_logger(__name__)

REQUIRED_MARKETDATA_PROVIDER_VERSION = "2.17.0"


def ensure_marketdata_provider_version() -> None:
    import marketdata_provider

    actual = getattr(marketdata_provider, "__version__", None)
    if actual != REQUIRED_MARKETDATA_PROVIDER_VERSION:
        raise RuntimeError(
            "OpenPine requires marketdata-provider "
            f"{REQUIRED_MARKETDATA_PROVIDER_VERSION}; imported {actual!r}. "
            "Install the canonical marketdata-provider package."
        )


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


def normalize_provider_bar(provider_bar: Any, query: BarQuery) -> Bar:
    """Convert a provider bar into the canonical marketdata contract."""

    time = int(_attr_or_item(provider_bar, "time", "open_time_ms", "timestamp"))
    time_close = (
        int(_attr_or_item(provider_bar, "time_close", "close_time_ms"))
        if _has_any_field(provider_bar, ("time_close", "close_time_ms"))
        else close_time_ms(time, query.timeframe.canonical) + 1
    )
    exchange = (
        str(_attr_or_item(provider_bar, "exchange")).lower()
        if _has_field(provider_bar, "exchange")
        else query.instrument.exchange
    )
    market = (
        str(_attr_or_item(provider_bar, "market")).lower()
        if _has_field(provider_bar, "market")
        else query.instrument.market
    )
    symbol = (
        str(_attr_or_item(provider_bar, "symbol", "exchange_symbol")).upper()
        if _has_any_field(provider_bar, ("symbol", "exchange_symbol"))
        else query.instrument.symbol
    )
    return Bar(
        instrument=InstrumentKey(exchange=exchange, market=market, symbol=symbol),
        timeframe=query.timeframe,
        time=time,
        time_close=time_close,
        open=float(_attr_or_item(provider_bar, "open")),
        high=float(_attr_or_item(provider_bar, "high")),
        low=float(_attr_or_item(provider_bar, "low")),
        close=float(_attr_or_item(provider_bar, "close")),
        volume=float(_attr_or_item(provider_bar, "volume")) if _has_field(provider_bar, "volume") else None,
        closed=bool(_attr_or_item(provider_bar, "is_closed", "closed"))
        if _has_any_field(provider_bar, ("is_closed", "closed"))
        else True,
    )


def _coverage_for(query: BarQuery, bars: tuple[Bar, ...], source: str) -> CoverageReport:
    if not bars:
        return CoverageReport(
            requested_start_ms=query.start_ms,
            requested_end_ms=query.end_ms,
            delivered_start_ms=None,
            delivered_end_ms=None,
            missing_intervals=((query.start_ms, query.end_ms),),
            source_mix=(source,),
            status="empty",
        )
    duplicate_timestamps = tuple(
        sorted({bar.time for bar in bars if sum(1 for other in bars if other.time == bar.time) > 1})
    )
    ordered = all(bars[i].time < bars[i + 1].time for i in range(len(bars) - 1))
    status = "valid"
    if duplicate_timestamps:
        status = "duplicate"
    elif not ordered:
        status = "unordered"
    return CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=bars[-1].time_close,
        duplicate_timestamps=duplicate_timestamps,
        source_mix=(source,),
        status=status,
    )


@dataclass(frozen=True)
class LocalMarketDataProviderAdapter:
    """Provider adapter that fetches only through the canonical package API."""

    binance: BinanceConfig = BinanceConfig()
    bybit: BybitConfig = BybitConfig()

    provider_name = "marketdata-provider"

    def fetch_bars(self, query: BarQuery) -> BarSeries:
        ensure_marketdata_provider_version()
        raw_bars = self._fetch_raw_bars(query)
        bars = tuple(
            normalize_provider_bar(raw_bar, query)
            for raw_bar in raw_bars
        )
        return BarSeries(
            query=query,
            bars=bars,
            coverage=_coverage_for(query, bars, "provider"),
        )

    def _fetch_raw_bars(self, query: BarQuery) -> Iterable[Any]:
        exchange = query.instrument.exchange
        market = query.instrument.market
        symbol = query.instrument.symbol
        timeframe = query.timeframe.canonical
        if exchange == "binance":
            return binance_get_bars_sync(
                symbol,
                timeframe,
                query.start_ms,
                query.end_ms,
                self.binance,
                market=market,
            )
        if exchange == "bybit":
            bybit_market = "linear" if market in {"usdm", "linear"} else market
            return bybit_get_bars_sync(
                symbol,
                timeframe,
                query.start_ms,
                query.end_ms,
                self.bybit,
                market=bybit_market,
            )
        raise ValueError(f"unsupported marketdata-provider exchange: {exchange}")


def create_local_marketdata_provider_adapter() -> LocalMarketDataProviderAdapter:
    ensure_marketdata_provider_version()
    return LocalMarketDataProviderAdapter()


__all__ = [
    "LocalMarketDataProviderAdapter",
    "create_local_marketdata_provider_adapter",
    "ensure_marketdata_provider_version",
    "normalize_provider_bar",
]
