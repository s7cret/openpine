import json
from types import SimpleNamespace

from marketdata_provider.contracts import BarQuery, InstrumentKey, parse_timeframe

from openpine.data import orchestrator, provider_adapter
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


def test_provider_factory_binds_admitted_marketdata_identity(monkeypatch, tmp_path):
    manifest_hash = "sha256:" + "2" * 64
    marketdata_commit = "1" * 40
    manifest_path = tmp_path / "candidate.json"
    manifest_path.write_text(
        json.dumps(
            {
                "stage": "wheel-bound",
                "not_a_release": True,
                "manifest_hash": manifest_hash,
                "components": {
                    "marketdata-provider": {"sha": marketdata_commit},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENPINE_CANDIDATE_MANIFEST", str(manifest_path))
    captured = {}
    fake_provider = SimpleNamespace(persists_fetches=False)

    def create_provider(config):
        captured["config"] = config
        return fake_provider

    monkeypatch.setattr(provider_adapter, "create_provider", create_provider)

    assert provider_adapter.create_local_marketdata_provider_adapter() is fake_provider
    identity = captured["config"].artifact_identity
    assert identity.producer_commit == marketdata_commit
    assert identity.stack_id == manifest_hash
    assert fake_provider.persists_fetches is True


def test_default_candle_store_binds_admitted_marketdata_identity(monkeypatch, tmp_path):
    manifest_hash = "sha256:" + "4" * 64
    marketdata_commit = "3" * 40
    manifest_path = tmp_path / "candidate.json"
    manifest_path.write_text(
        json.dumps(
            {
                "stage": "wheel-bound",
                "not_a_release": True,
                "manifest_hash": manifest_hash,
                "components": {
                    "marketdata-provider": {"sha": marketdata_commit},
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("OPENPINE_CANDIDATE_MANIFEST", str(manifest_path))
    captured = {}
    fake_store = object()

    def create_candle_store(config):
        captured["config"] = config
        return fake_store

    monkeypatch.setattr(orchestrator, "create_candle_store", create_candle_store)

    assert orchestrator._default_candle_store() is fake_store
    identity = captured["config"].artifact_identity
    assert identity.producer_commit == marketdata_commit
    assert identity.stack_id == manifest_hash
