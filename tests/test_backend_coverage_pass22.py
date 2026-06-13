from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.gateway.routes import strategies as routes
from openpine.gateway.schemas import CompareTvRequest, StrategyCreate, StrategyMode, StrategyUpdate
from openpine.registry.strategies import StrategyInstance


def _strategy(strategy_id: str = "s1", *, status: str = "paused", enabled: bool = False) -> StrategyInstance:
    return StrategyInstance(
        strategy_id=strategy_id,
        name="Name",
        pine_id="pine",
        artifact_id="artifact",
        params_json='{"x":1}',
        params_hash="ph",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="1m",
        mode="paper",
        status=status,
        enabled=enabled,
        created_at=1,
        updated_at=2,
    )


class Registry:
    def __init__(self) -> None:
        self.items = {"s1": _strategy("s1"), "err": _strategy("err", status="error")}
        self.calls: list[tuple] = []
        self._conn = self
    def list_strategies(self): return list(self.items.values())
    def get_strategy(self, strategy_id):
        if strategy_id not in self.items: raise KeyError(strategy_id)
        return self.items[strategy_id]
    def create_strategy(self, **kw):
        strategy = _strategy("created", status="paused")
        for key, value in kw.items():
            if hasattr(strategy, key): setattr(strategy, key, value)
        strategy.strategy_id = "created"
        self.items[strategy.strategy_id] = strategy
        return strategy
    def set_enabled(self, strategy_id, enabled): self.calls.append(("enabled", strategy_id, enabled)); self.items[strategy_id].enabled = enabled
    def update_mode(self, strategy_id, mode): self.calls.append(("mode", strategy_id, mode)); self.items[strategy_id].mode = mode
    def update_status(self, strategy_id, status): self.calls.append(("status", strategy_id, status)); self.items[strategy_id].status = status
    def execute(self, sql, values):
        strategy_id = values[-1]
        strategy = self.items[strategy_id]
        set_clause = sql.split(" SET ", 1)[1].split(" WHERE ", 1)[0]
        fields = [part.split("=", 1)[0].strip() for part in set_clause.split(",")]
        for field, value in zip(fields, values[:-1]):
            if hasattr(strategy, field):
                setattr(strategy, field, value)
        return SimpleNamespace()
    def commit(self): pass
    def delete_strategy(self, strategy_id):
        if strategy_id not in self.items: raise KeyError(strategy_id)
        del self.items[strategy_id]


class Storage:
    def __init__(self) -> None: self.fail_counts = False
    def execute(self, sql, params=()):
        if self.fail_counts and "COUNT" in sql: raise RuntimeError("db")
        rows = []
        if "SELECT run_id FROM backtest_runs" in sql: rows = [("run1",), ("run2",)]
        elif "COUNT" in sql: rows = [(2,)]
        return SimpleNamespace(fetchone=lambda: rows[0] if rows else (0,), fetchall=lambda: rows)


class PineRegistry:
    def __init__(self, exists: bool = True): self.exists = exists
    def get_source(self, pine_id):
        if not self.exists: raise KeyError(pine_id)
        return SimpleNamespace(id=pine_id)


class ArtifactStore:
    def __init__(self, ok: bool = True, status: str = "OK") -> None:
        self.ok = ok; self.status = status
    def get_artifact(self, artifact_id, pine_id):
        if not self.ok: raise FileNotFoundError(artifact_id)
        return {"compile_meta": {"compile_status": self.status}}


@pytest.mark.asyncio
async def test_strategy_routes_crud_actions_preview_and_compare(tmp_path: Path):
    registry = Registry()
    state = SimpleNamespace(strategy_registry=registry, pine_registry=PineRegistry(), artifact_store=ArtifactStore(), storage=Storage())

    listed = await routes.list_strategies(registry)
    assert len(listed) >= 2
    body = StrategyCreate(name="Created", pine_id="pine", artifact_id="artifact", symbol="ETHUSDT", timeframe="5m", exchange="binance", market_type="spot", params_json='{"a":2}', mode=StrategyMode.PAPER)
    created = await routes.create_strategy(body, state)
    assert created.strategy_id == "created" and created.symbol == "ETHUSDT"
    assert (await routes.get_strategy("created", registry)).name == "Created"
    with pytest.raises(HTTPException):
        await routes.get_strategy("missing", registry)
    with pytest.raises(HTTPException):
        await routes.create_strategy(body, SimpleNamespace(strategy_registry=registry, pine_registry=PineRegistry(False), artifact_store=ArtifactStore(), storage=Storage()))
    with pytest.raises(HTTPException):
        await routes.create_strategy(body, SimpleNamespace(strategy_registry=registry, pine_registry=PineRegistry(), artifact_store=ArtifactStore(False), storage=Storage()))
    with pytest.raises(HTTPException):
        await routes.create_strategy(body, SimpleNamespace(strategy_registry=registry, pine_registry=PineRegistry(), artifact_store=ArtifactStore(True, "ERROR"), storage=Storage()))

    unchanged = await routes.update_strategy("created", StrategyUpdate(), state)
    assert unchanged.strategy_id == "created"
    updated = await routes.update_strategy("created", StrategyUpdate(name="New", enabled=True, mode=StrategyMode.LIVE, params_json='{"b":3}'), state)
    assert updated.name == "New" and updated.enabled is True and updated.mode == "live"
    with pytest.raises(HTTPException):
        await routes.update_strategy("missing", StrategyUpdate(name="x"), state)

    assert (await routes.strategy_action("created", state, action="start"))["status"] == "ok"
    assert (await routes.strategy_action("created", state, action="pause"))["status"] == "ok"
    assert (await routes.strategy_action("created", state, action="enable"))["status"] == "ok"
    assert (await routes.strategy_action("err", state, action="clear_error"))["status"] == "ok"
    registry.items["err"].status = "error"
    with pytest.raises(HTTPException):
        await routes.strategy_action("err", state, action="start")
    with pytest.raises(HTTPException):
        await routes.strategy_action("created", state, action="clear_error")
    with pytest.raises(HTTPException):
        await routes.strategy_action("created", state, action="unknown")
    with pytest.raises(HTTPException):
        await routes.strategy_action("missing", state, action="pause")

    preview = await routes.delete_strategy_preview("created", state)
    assert preview["resources"]["backtest_runs"] == 2
    with pytest.raises(HTTPException):
        await routes.delete_strategy_preview("missing", state)
    await routes.delete_strategy("created", registry)
    with pytest.raises(HTTPException):
        await routes.delete_strategy("missing", registry)

    op = tmp_path / "op.csv"; tv = tmp_path / "tv.csv"
    op.write_text("time,plot,open\n1,2.0,99\n2,3.1,98\n", encoding="utf-8")
    tv.write_text("bar_time,plot,open\n1,2.0,99\n2,3.0,98\n", encoding="utf-8")
    req = CompareTvRequest(openpine_plots_path=str(op), tv_chart_path=str(tv), abs_tol=0.05, include_base_columns=False)
    comp = await routes.strategy_compare_tv("s1", req, state)
    assert comp["status"] == "mismatch" and comp["mismatch_cells"] == 1
    req2 = CompareTvRequest(openpine_plots_path=str(op), tv_chart_path=str(tmp_path / "missing.csv"))
    with pytest.raises(HTTPException):
        await routes.strategy_compare_tv("s1", req2, state)
    empty = tmp_path / "empty.csv"; empty.write_text("time,x\n9,1\n", encoding="utf-8")
    req3 = CompareTvRequest(openpine_plots_path=str(op), tv_chart_path=str(empty))
    assert (await routes.strategy_compare_tv("s1", req3, state))["status"] == "error"

    assert (await routes.strategy_enable("s1", registry))["enabled"] == "true"
    assert (await routes.strategy_disable("s1", registry))["enabled"] == "false"
    with pytest.raises(HTTPException):
        await routes.strategy_enable("missing", registry)
    with pytest.raises(HTTPException):
        await routes.strategy_disable("missing", registry)
