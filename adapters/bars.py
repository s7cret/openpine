"""Single production bar conversion boundary for OpenPine."""

from __future__ import annotations

from typing import Any, Iterable

from marketdata_provider.contracts import Bar, BarQuery, BarSeries

from openpine.data.provider_adapter import _coverage_for, normalize_provider_bar


def from_provider_bars(raw: Iterable[Any], query: BarQuery) -> BarSeries:
    """Normalize provider-ish rows into the canonical marketdata BarSeries."""

    bars = tuple(normalize_provider_bar(bar, query) for bar in raw)
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
