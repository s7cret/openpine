from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openpine.gateway.routes import tv_parity
from openpine.registry.strategies import StrategyInstance


def _strategy(*, semantic_profile: str | None = "strict_5x") -> StrategyInstance:
    return StrategyInstance(
        strategy_id="strat_1",
        name="Strategy",
        pine_id="pine_1",
        artifact_id="art_1",
        params_json="{}",
        params_hash="params_1",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="1m",
        mode="paper",
        status="paused",
        enabled=False,
        created_at=1,
        updated_at=2,
        semantic_profile=semantic_profile,
    )


def test_tv_parity_config_uses_strategy_semantic_profile() -> None:
    config = tv_parity._backtest_config_for_tv_replay(
        strategy=_strategy(semantic_profile="strict_5x"),
        from_ms=1,
        to_ms=2,
        warmup_bars=0,
        capture_plots=False,
        decl_args={},
    )
    assert config.semantic_profile == "strict_5x"


def test_tv_parity_persist_uses_admitted_profile(tmp_path, monkeypatch) -> None:
    captured: dict[str, object] = {}

    def persist(state, **kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr("openpine.gateway.side_effects.persist_gateway_job", persist)
    monkeypatch.setattr(tv_parity, "_run_tv_parity_background", lambda **kwargs: None)
    state = SimpleNamespace(
        config=SimpleNamespace(data_dir=tmp_path),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        backtest_store=SimpleNamespace(create_run=lambda request: "run_tv_sem"),
    )
    app = FastAPI()
    app.include_router(tv_parity.router, prefix="/api")
    from openpine.gateway.deps import get_state

    app.dependency_overrides[get_state] = lambda: state
    client = TestClient(app)
    response = client.post(
        "/api/tv-parity/run",
        data={"strategy_id": "strat_1", "semantic_profile": "strict_5x"},
        files={
            "candles_file": (
                "candles.csv",
                "time,open,high,low,close,volume\n1704067200000,1,2,0.5,1.5,10\n",
                "text/csv",
            )
        },
    )
    assert response.status_code == 200
    assert captured.get("semantic_profile") == "strict_5x"
    assert "semantic_profile:legacy_4x" not in list(captured.get("input_artifact_refs") or [])
