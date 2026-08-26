from __future__ import annotations

from types import SimpleNamespace

import pytest
from pydantic import ValidationError

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


def _state(registry: Registry) -> SimpleNamespace:
    return SimpleNamespace(
        strategy_registry=registry,
        pine_registry=SimpleNamespace(get_source=lambda pine_id: SimpleNamespace(id=pine_id)),
        artifact_store=SimpleNamespace(
            get_artifact=lambda artifact_id, pine_id: {"compile_meta": {"compile_status": "OK"}}
        ),
    )


@pytest.mark.asyncio
async def test_create_strategy_uses_current_semantic_profile_automatically() -> None:
    registry = Registry()

    created = await routes.create_strategy(
        StrategyCreate(
            name="Created",
            pine_id="pine",
            artifact_id="artifact",
            symbol="ETHUSDT",
            timeframe="5m",
        ),
        _state(registry),
    )

    assert registry.created.get("semantic_profile") == "strict_5x"
    assert created.semantic_profile == "strict_5x"


@pytest.mark.parametrize("profile", ["legacy_4x", "nope"])
def test_create_strategy_rejects_semantic_profile_override(profile: str) -> None:
    with pytest.raises(ValidationError):
        StrategyCreate(
            name="Created",
            pine_id="pine",
            artifact_id="artifact",
            symbol="ETHUSDT",
            timeframe="5m",
            semantic_profile=profile,
        )
