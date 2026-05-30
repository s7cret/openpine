from types import SimpleNamespace

from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

from openpine.data.provider_adapter import normalize_provider_bar


def test_normalize_provider_bar_uses_canonical_contract_shape():
    query = BarQuery(
        instrument=InstrumentKey(exchange="binance", market="spot", symbol="BTCUSD"),
        timeframe=parse_timeframe("15"),
        start_ms=0,
        end_ms=3_000,
        source="provider",
    )

    bar = normalize_provider_bar(
        SimpleNamespace(
            symbol="BTCUSD",
            exchange="BINANCE",
            market="spot",
            time=1_000,
            time_close=2_000,
            open=10,
            high=11,
            low=9,
            close=10,
            volume=5,
            is_closed=True,
        ),
        query,
    )

    assert bar.instrument.serialize() == "binance/spot/BTCUSD"
    assert bar.time == 1_000
    assert bar.time_close == 2_000
    assert bar.volume == 5.0
