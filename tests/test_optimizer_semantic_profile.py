from __future__ import annotations

from types import SimpleNamespace

import pytest

from openpine.gateway.routes.optimizer import optimizer_dry_run
from openpine.gateway.schemas import OptimizerDryRunRequest
from openpine.registry.strategies import StrategyInstance


def _strategy(*, semantic_profile: str | None = "strict_5x") -> StrategyInstance:
    return StrategyInstance(
        strategy_id="s1",
        name="Strategy",
        pine_id="pine",
        artifact_id="artifact",
        params_json="{}",
        params_hash="ph",
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


@pytest.mark.asyncio
async def test_optimizer_dry_run_persists_admitted_semantic_profile(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _Service:
        def validate_config(self, strategy_id, trials):
            return SimpleNamespace(
                strategy_id=strategy_id,
                trials_requested=trials,
                status="ok",
                reason=None,
            )

    monkeypatch.setattr("openpine.optimizer.OptimizerService", _Service)

    def persist(state, **kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr("openpine.gateway.side_effects.persist_gateway_job", persist)
    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy())
    )
    resp = await optimizer_dry_run(
        OptimizerDryRunRequest(strategy_id="s1", trials=2),
        state,
    )
    assert resp.status == "ok"
    assert captured.get("semantic_profile") == "strict_5x"
    assert captured.get("kind") == "optimize"
