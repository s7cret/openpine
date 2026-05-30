from __future__ import annotations

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe

from openpine.runtime.engine import BacktestEngineAdapter


def test_engine_bar_adapter_preserves_missing_volume() -> None:
    timeframe = parse_timeframe("15m")
    bar = Bar(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT"),
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

    engine_bar = BacktestEngineAdapter()._to_engine_bar(bar)

    assert engine_bar.volume is None
    assert engine_bar.time == bar.time
    assert engine_bar.time_close == bar.time_close
