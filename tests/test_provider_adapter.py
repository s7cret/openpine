from types import SimpleNamespace

from openpine.contracts import BarQuery, InstrumentKey, Timeframe
from openpine.data.provider_adapter import LocalMarketDataProviderAdapter


class _Provider:
    def get_bars(self, *args, **kwargs):
        del args, kwargs
        return [
            SimpleNamespace(
                symbol="BTCUSD",
                exchange="BINANCE",
                timeframe="15",
                time=1_000,
                open=10,
                high=10,
                low=10,
                close=10,
                volume=0,
                closed=True,
            ),
            SimpleNamespace(
                symbol="BTCUSD",
                exchange="BINANCE",
                timeframe="15",
                time=2_000,
                open=11,
                high=12,
                low=10,
                close=11,
                volume=5,
                closed=True,
            ),
        ]


def test_local_provider_filters_synthetic_empty_bars():
    query = BarQuery(
        instrument_key=InstrumentKey(symbol="BTCUSD", exchange="BINANCE", market_type="spot"),
        timeframe=Timeframe(value="15"),
        start_ms=0,
        end_ms=3_000,
    )

    bars = LocalMarketDataProviderAdapter(_Provider()).get_bars(query)

    assert [bar.timestamp for bar in bars] == [2_000]
