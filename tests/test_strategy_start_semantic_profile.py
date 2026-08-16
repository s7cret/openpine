from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.gateway.routes import strategies as routes
from openpine.registry.strategies import StrategyInstance


def _strategy(*, semantic_profile: str | None = None, mode: str = "paper") -> StrategyInstance:
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
        mode=mode,
        status="paused",
        enabled=False,
        created_at=1,
        updated_at=2,
        semantic_profile=semantic_profile,
    )


class Registry:
    def __init__(self, item: StrategyInstance) -> None:
        self.item = item
        self.activated = False

    def get_strategy(self, strategy_id: str) -> StrategyInstance:
        if strategy_id != self.item.strategy_id:
            raise KeyError(strategy_id)
        return self.item

    def activate_strategy(self, strategy_id: str, **kwargs: object) -> None:
        self.activated = True
        for key, value in kwargs.items():
            if value is not None:
                setattr(self.item, key, value)
        self.item.enabled = True


@pytest.mark.asyncio
async def test_start_rejects_missing_semantic_profile() -> None:
    registry = Registry(_strategy())
    state = SimpleNamespace(strategy_registry=registry)
    with pytest.raises(HTTPException) as excinfo:
        await routes.strategy_action("s1", state, action="start")
    assert excinfo.value.status_code == 403
    assert registry.activated is False
    assert registry.item.status == "paused"


@pytest.mark.asyncio
async def test_enable_rejects_missing_semantic_profile() -> None:
    registry = Registry(_strategy())
    state = SimpleNamespace(strategy_registry=registry)
    with pytest.raises(HTTPException) as excinfo:
        await routes.strategy_action("s1", state, action="enable")
    assert excinfo.value.status_code == 403
    assert registry.activated is False


@pytest.mark.asyncio
async def test_start_admits_stamped_semantic_profile() -> None:
    registry = Registry(_strategy(semantic_profile="strict_5x"))
    state = SimpleNamespace(strategy_registry=registry)
    result = await routes.strategy_action("s1", state, action="start")
    assert result["status"] == "ok"
    assert registry.activated is True
