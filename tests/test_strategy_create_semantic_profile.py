from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.gateway.routes import strategies as routes
from openpine.gateway.schemas import StrategyCreate
from openpine.registry.strategies import StrategyInstance


def _strategy(**kwargs) -> StrategyInstance:
    values = dict(
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
    )
    values.update(kwargs)
    return StrategyInstance(**values)


class Registry:
    def __init__(self) -> None:
        self.created: dict[str, object] = {}

    def create_strategy(self, **kwargs):
        self.created = kwargs
        return _strategy(
            strategy_id="created",
            name=str(kwargs.get("name") or "Name"),
            semantic_profile=kwargs.get("semantic_profile"),
        )


@pytest.mark.asyncio
async def test_create_strategy_requires_semantic_profile() -> None:
    with pytest.raises(Exception):
        StrategyCreate(
            name="Created",
            pine_id="pine",
            artifact_id="artifact",
            symbol="ETHUSDT",
            timeframe="5m",
        )


@pytest.mark.asyncio
async def test_create_strategy_persists_admitted_profile() -> None:
    registry = Registry()
    state = SimpleNamespace(
        strategy_registry=registry,
        pine_registry=SimpleNamespace(get_source=lambda pine_id: SimpleNamespace(id=pine_id)),
        artifact_store=SimpleNamespace(
            get_artifact=lambda artifact_id, pine_id: {"compile_meta": {"compile_status": "OK"}}
        ),
    )
    created = await routes.create_strategy(
        StrategyCreate(
            name="Created",
            pine_id="pine",
            artifact_id="artifact",
            symbol="ETHUSDT",
            timeframe="5m",
            semantic_profile="strict_5x",
        ),
        state,
    )
    assert registry.created.get("semantic_profile") == "strict_5x"
    assert created.semantic_profile == "strict_5x"


@pytest.mark.asyncio
async def test_create_strategy_rejects_unknown_profile() -> None:
    registry = Registry()
    state = SimpleNamespace(
        strategy_registry=registry,
        pine_registry=SimpleNamespace(get_source=lambda pine_id: SimpleNamespace(id=pine_id)),
        artifact_store=SimpleNamespace(
            get_artifact=lambda artifact_id, pine_id: {"compile_meta": {"compile_status": "OK"}}
        ),
    )
    with pytest.raises(HTTPException):
        await routes.create_strategy(
            StrategyCreate(
                name="Created",
                pine_id="pine",
                artifact_id="artifact",
                symbol="ETHUSDT",
                timeframe="5m",
                semantic_profile="nope",
            ),
            state,
        )
