from __future__ import annotations

from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    InstrumentKey,
    parse_timeframe,
)

from openpine.adapters.bars import from_provider_bars, to_engine_bars


def test_bar_adapters_preserve_canonical_window_semantics() -> None:
    timeframe = parse_timeframe("15m")
    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
        timeframe=timeframe,
        start_ms=1_700_000_000_000,
        end_ms=1_700_001_800_000,
        source="provider",
    )
    bar = Bar(
        instrument=query.instrument,
        timeframe=timeframe,
        time=1_700_000_000_000,
        time_close=1_700_000_899_999,
        open=1.0,
        high=2.0,
        low=0.5,
        close=1.5,
        volume=None,
        closed=True,
    )
    series = BarSeries(
        query=query, bars=(bar,), coverage=from_provider_bars((bar,), query).coverage
    )

    engine_series = to_engine_bars(series)

    assert engine_series.get_bar(0).time == bar.time
    assert engine_series.get_bar(0).time_close == bar.time_close
    assert engine_series.get_bar(0).volume is None
