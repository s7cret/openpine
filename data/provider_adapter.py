"""OpenPine boundary around the canonical marketdata-provider package."""

from __future__ import annotations

from dataclasses import replace
from bisect import bisect_left
from pathlib import Path
from typing import Any

import structlog
from marketdata_provider import create_footprint_provider, create_provider
from marketdata_provider.config import MarketDataConfig
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    MarketDataProvider,
    parse_timeframe,
)

log = structlog.get_logger(__name__)

REQUIRED_MARKETDATA_PROVIDER_VERSION = "2.18.0"


class RuntimeDataProviderAdapter:
    """Pine runtime data provider backed by the canonical BarQuery provider."""

    def __init__(
        self,
        provider: MarketDataProvider,
        *,
        exchange: str,
        market: str,
        prefetch_end_ms: int | None = None,
    ) -> None:
        self._provider = provider
        from openpine.data.orchestrator import DataOrchestrator

        self._orchestrator = DataOrchestrator(provider=provider)
        self.exchange = exchange.lower()
        self.market = market.lower()
        self.prefetch_end_ms = prefetch_end_ms
        self._bars_cache: dict[tuple[str, str, str, str], tuple[int, int, list[Any], list[int]]] = {}

    def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start_ms: int | None,
        end_ms: int | None,
        *,
        max_bars: int | None = None,
        exchange: str | None = None,
        market: str | None = None,
    ) -> list[Any]:
        from pinelib.core.bar import from_contract_bar

        if start_ms is None or end_ms is None:
            raise ValueError("Pine runtime marketdata requests require bounded start/end")
        exchange_key = (exchange or self.exchange).lower()
        market_key = (market or self.market).lower()
        symbol_key = symbol.upper()
        timeframe_key = parse_timeframe(timeframe).canonical
        cache_key = (exchange_key, market_key, symbol_key, timeframe_key)
        cached = self._bars_cache.get(cache_key)
        if cached is not None:
            cached_start, cached_end, cached_bars, cached_times = cached
            if cached_start <= start_ms and cached_end >= end_ms:
                left = bisect_left(cached_times, start_ms)
                right = bisect_left(cached_times, end_ms)
                bars = cached_bars[left:right]
                return bars[:max_bars] if max_bars is not None else bars

        fetch_end_ms = max(end_ms, self.prefetch_end_ms or end_ms)
        timeframe_obj = parse_timeframe(timeframe_key)
        query = BarQuery(
            instrument=InstrumentKey(
                exchange=exchange_key,
                market=market_key,
                symbol=symbol_key,
            ),
            timeframe=timeframe_obj,
            start_ms=int(start_ms),
            end_ms=int(fetch_end_ms),
            gap_policy="allow_with_metadata",
        )
        fetched = [from_contract_bar(bar) for bar in self._orchestrator.load_bars(query).bars]
        fetched_times = [bar.time for bar in fetched]
        self._bars_cache[cache_key] = (start_ms, fetch_end_ms, fetched, fetched_times)
        left = bisect_left(fetched_times, start_ms)
        right = bisect_left(fetched_times, end_ms)
        bars = fetched[left:right]
        if max_bars is not None:
            bars = bars[:max_bars]
        return bars

    def get_intrabar_bars(
        self,
        symbol: str,
        chart_bar: Any,
        lower_timeframe: str | None = None,
        *,
        max_bars: int | None = None,
    ) -> list[Any]:
        timeframe = lower_timeframe or "1"
        start_ms = int(chart_bar.time)
        close_ms = getattr(chart_bar, "time_close", None)
        end_ms = int(close_ms) + 1 if close_ms is not None else start_ms
        return self.get_bars(
            symbol,
            timeframe,
            start_ms,
            end_ms,
            max_bars=max_bars,
        )


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


def create_local_runtime_data_provider_adapter(
    config: MarketDataConfig | None = None,
    *,
    cache_dir: Path | str | None = None,
    exchange: str = "binance",
    market: str = "spot",
    prefetch_end_ms: int | None = None,
) -> RuntimeDataProviderAdapter:
    """Create the Pine runtime provider used by request.security paths."""

    return RuntimeDataProviderAdapter(
        create_local_marketdata_provider_adapter(config=config, cache_dir=cache_dir),
        exchange=exchange,
        market=market,
        prefetch_end_ms=prefetch_end_ms,
    )


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
    "create_local_marketdata_provider_adapter",
    "create_local_runtime_data_provider_adapter",
    "create_local_footprint_provider_adapter",
    "ensure_marketdata_provider_version",
    "normalize_provider_bar",
]
