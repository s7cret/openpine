from marketdata_provider.contracts import FootprintBar, FootprintLevel, FootprintQuery, FootprintSeries, InstrumentKey, parse_timeframe
from marketdata_provider.contracts.series import CoverageReport
from marketdata_provider.store.footprint_store import FootprintStore

from openpine.data.footprint_orchestrator import FootprintOrchestrator
from openpine.data.provider_adapter import create_local_footprint_provider_adapter


def _query():
    return FootprintQuery(
        instrument=InstrumentKey("binance", "usdm", "BTCUSDT"),
        timeframe=parse_timeframe("1m"),
        start_ms=0,
        end_ms=60_000,
        price_bucket=10.0,
    )


def test_footprint_orchestrator_uses_separate_provider_and_store(tmp_path):
    query = _query()
    series = FootprintSeries(
        query=query,
        bars=(FootprintBar(0, 60_000, (FootprintLevel(100.0, 110.0, buy_volume=1.0),), 1),),
        coverage=CoverageReport(0, 60_000, 0, 60_000, source_mix=("footprint",)),
    )

    class Provider:
        def fetch_footprint(self, received):
            assert received is query
            return series

    store = FootprintStore(tmp_path)
    loaded = FootprintOrchestrator(provider=Provider(), store=store).load_footprints(query)

    assert loaded.bars == series.bars
    assert store.read(query).bars == series.bars


def test_create_local_footprint_provider_adapter_uses_marketdata_provider(tmp_path):
    provider = create_local_footprint_provider_adapter(cache_dir=tmp_path)

    assert hasattr(provider, "fetch_footprint")

