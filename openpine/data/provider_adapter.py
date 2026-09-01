"""OpenPine boundary around the canonical marketdata-provider package."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any

from marketdata_provider import create_footprint_provider, create_provider
from marketdata_provider.config import MarketDataConfig
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    CoverageReport,
    InstrumentKey,
    MarketDataProvider,
    parse_timeframe,
)

from openpine._compat import structlog
from openpine.data.row_helpers import attr_or_item, duplicate_timestamps, has_any_field, has_field

log = structlog.get_logger(__name__)

REQUIRED_MARKETDATA_PROVIDER_VERSION = "5.0.0rc6"


def ensure_marketdata_provider_version() -> None:
    import marketdata_provider

    actual = getattr(marketdata_provider, "__version__", None)
    if actual != REQUIRED_MARKETDATA_PROVIDER_VERSION:
        raise RuntimeError(
            "OpenPine requires marketdata-provider "
            f"{REQUIRED_MARKETDATA_PROVIDER_VERSION}; imported {actual!r}. "
            "Install the canonical marketdata-provider package."
        )








def normalize_provider_bar(provider_bar: Any, query: BarQuery) -> Bar:
    """Convert a provider-ish bar into the canonical marketdata contract.

    This is retained for ingestion tests and non-provider boundary inputs. Normal
    product provider calls use `marketdata_provider.create_provider`.
    """
    time = int(attr_or_item(provider_bar, "time", "open_time_ms", "timestamp"))
    time_close = (
        int(attr_or_item(provider_bar, "time_close", "close_time_ms"))
        if has_any_field(provider_bar, ("time_close", "close_time_ms"))
        else (
            time + query.timeframe.duration_ms
            if query.timeframe.duration_ms is not None
            else query.end_ms
        )
    )
    exchange = (
        str(attr_or_item(provider_bar, "exchange")).lower()
        if has_field(provider_bar, "exchange")
        else query.instrument.exchange
    )
    market = (
        str(attr_or_item(provider_bar, "market")).lower()
        if has_field(provider_bar, "market")
        else query.instrument.market
    )
    symbol = (
        str(attr_or_item(provider_bar, "symbol", "exchange_symbol")).upper()
        if has_any_field(provider_bar, ("symbol", "exchange_symbol"))
        else query.instrument.symbol
    )
    volume = (
        attr_or_item(provider_bar, "volume")
        if has_field(provider_bar, "volume")
        else None
    )
    return Bar(
        instrument=InstrumentKey(exchange=exchange, market=market, symbol=symbol),
        timeframe=query.timeframe,
        time=time,
        time_close=time_close,
        open=float(attr_or_item(provider_bar, "open")),
        high=float(attr_or_item(provider_bar, "high")),
        low=float(attr_or_item(provider_bar, "low")),
        close=float(attr_or_item(provider_bar, "close")),
        volume=None if volume is None else float(volume),
        closed=(
            bool(attr_or_item(provider_bar, "is_closed", "closed"))
            if has_any_field(provider_bar, ("is_closed", "closed"))
            else True
        ),
    )


def _coverage_for(
    query: BarQuery, bars: tuple[Bar, ...], source: str
) -> CoverageReport:
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
    duplicate_timestamps_ = duplicate_timestamps(bars)
    ordered = all(bars[i].time < bars[i + 1].time for i in range(len(bars) - 1))
    status = "valid"
    if duplicate_timestamps_:
        status = "duplicate"
    elif not ordered:
        status = "unordered"
    return CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=bars[-1].time_close,
        duplicate_timestamps=duplicate_timestamps_,
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
    provider = create_provider(cfg)
    provider.persists_fetches = True
    return provider


def create_local_footprint_provider_adapter(
    config: MarketDataConfig | None = None,
    *,
    cache_dir: Path | str | None = None,
):
    """Create the canonical marketdata-provider footprint adapter for OpenPine."""

    ensure_marketdata_provider_version()
    cfg = config or MarketDataConfig()
    if cache_dir is not None:
        cfg = replace(cfg, storage=replace(cfg.storage, cache_dir=Path(cache_dir)))
    return create_footprint_provider(cfg)


__all__ = [
    "create_local_footprint_provider_adapter",
    "create_local_marketdata_provider_adapter",

    "ensure_marketdata_provider_version",
    "normalize_provider_bar",
]
