"""Single production bar conversion boundary for OpenPine."""

from __future__ import annotations

from typing import Any, Iterable

from marketdata_provider.contracts import Bar, BarQuery, BarSeries, CoverageReport, InstrumentKey


def from_provider_bars(raw: Iterable[Any], query: BarQuery) -> BarSeries:
    """Normalize provider-ish rows into the canonical marketdata BarSeries."""

    bars = tuple(_normalize_provider_bar(row, query) for row in raw)
    return BarSeries(query=query, bars=bars, coverage=_coverage_for(query, bars, "provider"))


def to_engine_bar(bar: Bar) -> Any:
    """Convert one canonical marketdata bar to BacktestEngine's runtime bar."""

    from backtest_engine.models import Bar as EngineBar

    return EngineBar(
        time=int(bar.time),
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=None if bar.volume is None else float(bar.volume),
        time_close=int(bar.time_close),
    )


def to_engine_bars(series: BarSeries) -> Any:
    """Convert a canonical marketdata series to BacktestEngine's BarSeries."""

    from backtest_engine.models import BarSeries as EngineBarSeries

    return EngineBarSeries.from_bars(to_engine_bar(bar) for bar in series.bars)


def to_pinelib_bar(bar: Bar) -> Any:
    """Convert one canonical marketdata bar to PineLib's runtime bar."""

    from pinelib.core.bar import Bar as PineBar

    return PineBar(
        time=int(bar.time),
        open=float(bar.open),
        high=float(bar.high),
        low=float(bar.low),
        close=float(bar.close),
        volume=0.0 if bar.volume is None else float(bar.volume),
        time_close=int(bar.time_close),
    )


def to_pinelib_bars(series: BarSeries) -> tuple[Any, ...]:
    """Convert a canonical marketdata series to PineLib runtime bars."""

    return tuple(to_pinelib_bar(bar) for bar in series.bars)


def _normalize_provider_bar(row: Any, query: BarQuery) -> Bar:
    time = int(_attr_or_item(row, "time", "open_time_ms", "timestamp"))
    time_close = (
        int(_attr_or_item(row, "time_close", "close_time_ms"))
        if _has_any(row, ("time_close", "close_time_ms"))
        else time + query.timeframe.duration_ms
        if query.timeframe.duration_ms is not None
        else query.end_ms
    )
    exchange = str(_attr_or_item(row, "exchange")) if _has(row, "exchange") else query.instrument.exchange
    market = str(_attr_or_item(row, "market")) if _has(row, "market") else query.instrument.market
    symbol = str(_attr_or_item(row, "symbol", "exchange_symbol")) if _has_any(row, ("symbol", "exchange_symbol")) else query.instrument.symbol
    volume = _attr_or_item(row, "volume") if _has(row, "volume") else None
    closed = bool(_attr_or_item(row, "closed", "is_closed")) if _has_any(row, ("closed", "is_closed")) else True
    return Bar(
        instrument=InstrumentKey(exchange=exchange.lower(), market=market.lower(), symbol=symbol.upper()),
        timeframe=query.timeframe,
        time=time,
        time_close=time_close,
        open=float(_attr_or_item(row, "open")),
        high=float(_attr_or_item(row, "high")),
        low=float(_attr_or_item(row, "low")),
        close=float(_attr_or_item(row, "close")),
        volume=None if volume is None else float(volume),
        closed=closed,
    )


def _coverage_for(query: BarQuery, bars: tuple[Bar, ...], source: str) -> CoverageReport:
    if not bars:
        return CoverageReport(query.start_ms, query.end_ms, None, None, ((query.start_ms, query.end_ms),), (), (source,), "empty")
    duration_ms = query.timeframe.duration_ms
    delivered = {bar.time for bar in bars}
    missing = (
        tuple((start_ms, min(start_ms + duration_ms, query.end_ms)) for start_ms in range(query.start_ms, query.end_ms, duration_ms) if start_ms not in delivered)
        if duration_ms is not None
        else ()
    )
    duplicates = _duplicate_timestamps(bars)
    ordered = all(bars[index].time < bars[index + 1].time for index in range(len(bars) - 1))
    status = "duplicate" if duplicates else "unordered" if not ordered else "gap" if missing else "valid"
    return CoverageReport(
        requested_start_ms=query.start_ms,
        requested_end_ms=query.end_ms,
        delivered_start_ms=bars[0].time,
        delivered_end_ms=max(bar.time_close for bar in bars),
        missing_intervals=missing if ordered and not duplicates else (),
        duplicate_timestamps=duplicates,
        source_mix=(source,),
        status=status,
    )


def _duplicate_timestamps(bars: tuple[Bar, ...]) -> tuple[int, ...]:
    seen: set[int] = set()
    duplicates: set[int] = set()
    for bar in bars:
        if bar.time in seen:
            duplicates.add(bar.time)
        seen.add(bar.time)
    return tuple(sorted(duplicates))


def _attr_or_item(obj: Any, *names: str) -> Any:
    for name in names:
        if isinstance(obj, dict) and name in obj:
            return obj[name]
        if hasattr(obj, name):
            return getattr(obj, name)
    raise AttributeError(f"missing any of: {', '.join(names)}")


def _has(obj: Any, name: str) -> bool:
    return (isinstance(obj, dict) and name in obj) or hasattr(obj, name)


def _has_any(obj: Any, names: tuple[str, ...]) -> bool:
    return any(_has(obj, name) for name in names)


__all__ = [
    "from_provider_bars",
    "to_engine_bar",
    "to_engine_bars",
    "to_pinelib_bar",
    "to_pinelib_bars",
]

