"""OpenPine boundary around the canonical marketdata-provider package."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

import structlog
from marketdata_provider import create_provider
from marketdata_provider.config import MarketDataConfig
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    MarketDataProvider,
)

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
    """Convert a provider-ish bar into the canonical marketdata contract.

    This is retained for ingestion tests and non-provider boundary inputs. Normal
    product provider calls use `marketdata_provider.create_provider`.
    """
    time = int(_attr_or_item(provider_bar, "time", "open_time_ms", "timestamp"))
    time_close = (
        int(_attr_or_item(provider_bar, "time_close", "close_time_ms"))
        if _has_any_field(provider_bar, ("time_close", "close_time_ms"))
        else time + query.timeframe.duration_ms
        if query.timeframe.duration_ms is not None
        else query.end_ms
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
    volume = _attr_or_item(provider_bar, "volume") if _has_field(provider_bar, "volume") else None
    return Bar(
        instrument=InstrumentKey(exchange=exchange, market=market, symbol=symbol),
        timeframe=query.timeframe,
        time=time,
        time_close=time_close,
        open=float(_attr_or_item(provider_bar, "open")),
        high=float(_attr_or_item(provider_bar, "high")),
        low=float(_attr_or_item(provider_bar, "low")),
        close=float(_attr_or_item(provider_bar, "close")),
        volume=None if volume is None else float(volume),
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


def create_local_marketdata_provider_adapter(
    config: MarketDataConfig | None = None,
    *,
    cache_dir: Path | str | None = None,
) -> MarketDataProvider:
    """Create the canonical marketdata-provider adapter for OpenPine."""

    ensure_marketdata_provider_version()
    cfg = config or MarketDataConfig()
    if cache_dir is not None:
        cfg = replace(cfg, storage=replace(cfg.storage, cache_dir=Path(cache_dir)))
    return create_provider(cfg)


__all__ = [
    "create_local_marketdata_provider_adapter",
    "ensure_marketdata_provider_version",
    "normalize_provider_bar",
]
