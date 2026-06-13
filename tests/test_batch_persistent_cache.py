from pathlib import Path

from marketdata_provider.contracts import Bar, InstrumentKey, parse_timeframe

from openpine.batch.persistent_cache import (
    load_bars,
    load_tv_corpus,
    path_fingerprint,
    save_bars,
    save_tv_corpus,
)


def test_bar_cache_round_trips_exact_query(tmp_path: Path) -> None:
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe("15m")
    key = {
        "kind": "calculation_bars",
        "symbol": "BTCUSDT",
        "exchange": "BINANCE",
        "market_type": "spot",
        "timeframe": "15m",
        "calculation_from": 1,
        "calculation_to": 2,
        "gap_policy": "fail",
    }
    bars = [
        Bar(
            instrument=instrument,
            timeframe=timeframe,
            time=1,
            time_close=2,
            open=10.0,
            high=11.0,
            low=9.0,
            close=10.5,
            volume=12.0,
            closed=True,
        )
    ]

    save_bars(tmp_path, key, bars)
    cached = load_bars(tmp_path, key, instrument=instrument, timeframe=timeframe)

    assert cached is not None
    loaded, meta = cached
    assert meta["cache_hit"] is True
    assert loaded == bars
    assert (
        load_bars(
            tmp_path,
            {**key, "calculation_to": 3},
            instrument=instrument,
            timeframe=timeframe,
        )
        is None
    )


def test_tv_corpus_cache_round_trips_with_fingerprint(tmp_path: Path) -> None:
    chart = tmp_path / "chart.csv"
    chart.write_text("time,open,high,low,close\n1,1,2,0,1.5\n", encoding="utf-8")
    key = {
        "kind": "tv_corpus_visible_bars",
        "root": str(tmp_path),
        "source_group": "old_batch_1_1500",
        "timeframe": "15m",
        "symbol": "BTCUSDT",
        "charts": 1,
        "fingerprint": path_fingerprint([chart], root=tmp_path),
    }
    bars = {1: {"open": 1.0, "high": 2.0, "low": 0.0, "close": 1.5, "volume": 0.0}}

    save_tv_corpus(tmp_path, key, bars, {"rows_loaded": 1})
    cached = load_tv_corpus(tmp_path, key)

    assert cached is not None
    loaded, meta = cached
    assert loaded == bars
    assert meta["cache_hit"] is True
