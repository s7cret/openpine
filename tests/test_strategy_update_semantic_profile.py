from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from pydantic import ValidationError

from openpine.gateway.routes import strategies as routes
from openpine.gateway.schemas import StrategyUpdate
from openpine.registry.strategies import StrategyInstance


def _strategy(*, semantic_profile: str | None = "legacy_4x") -> StrategyInstance:
    return StrategyInstance(
        strategy_id="s1",
        name="Name",
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


class Registry:
    def __init__(self) -> None:
        self.item = _strategy()
        self.patched: dict[str, object] = {}

    def get_strategy(self, strategy_id: str) -> StrategyInstance:
        if strategy_id != self.item.strategy_id:
            raise KeyError(strategy_id)
        return self.item

    def patch_strategy_atomic(self, strategy_id: str, updates: dict[str, object]) -> None:
        self.patched = dict(updates)
        for key, value in updates.items():
            setattr(self.item, key, value)


@pytest.mark.asyncio
async def test_update_strategy_rejects_unknown_semantic_profile() -> None:
    with pytest.raises(ValidationError):
        StrategyUpdate()
    registry = Registry()
    state = SimpleNamespace(strategy_registry=registry)
    with pytest.raises(HTTPException) as excinfo:
        await routes.update_strategy(
            "s1",
            StrategyUpdate(semantic_profile="nope"),
            state,
        )
    assert excinfo.value.status_code == 403
    assert registry.patched == {}


@pytest.mark.asyncio
async def test_update_strategy_persists_admitted_semantic_profile() -> None:
    registry = Registry()
    state = SimpleNamespace(strategy_registry=registry)
    updated = await routes.update_strategy(
        "s1",
        StrategyUpdate(semantic_profile="strict_5x"),
        state,
    )
    assert registry.patched.get("semantic_profile") == "strict_5x"
    assert updated.semantic_profile == "strict_5x"
