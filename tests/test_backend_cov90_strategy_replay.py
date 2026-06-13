from __future__ import annotations

import asyncio
import sys
import types
from types import SimpleNamespace

import pytest

from openpine.gateway.routes import strategies as sr


def _strategy():
    return SimpleNamespace(
        strategy_id="s1",
        name="S",
        pine_id="p",
        artifact_id="a",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        params_json="{}",
        params_hash="h",
        mode="backtest",
        enabled=True,
        status="paused",
        created_at=1,
        updated_at=2,
    )


class Registry:
    def __init__(self, strategy=None):
        self.strategy = strategy or _strategy()
        self.statuses = []

    def get_strategy(self, strategy_id):
        if strategy_id == "missing":
            raise KeyError(strategy_id)
        return self.strategy

    def update_status(self, strategy_id, status):
        self.statuses.append((strategy_id, status))


def test_strategy_replay_success_and_failure(monkeypatch):
    broadcasts = []

    async def broadcast(update):
        broadcasts.append(update)

    import openpine.gateway.ws_manager as ws_mod
    monkeypatch.setattr(ws_mod.ws_manager, "broadcast", broadcast)

    md_contracts = types.ModuleType("marketdata_provider.contracts")
    md_contracts.InstrumentKey = lambda **kw: SimpleNamespace(**kw)
    md_contracts.BarQuery = lambda **kw: SimpleNamespace(**kw)
    md_contracts.parse_timeframe = lambda tf: SimpleNamespace(value=tf, duration_ms=60_000)
    monkeypatch.setitem(sys.modules, "marketdata_provider.contracts", md_contracts)

    data_orch = types.ModuleType("openpine.data.orchestrator")

    class DataOrchestrator:
        def get_bars(self, query):
            return [SimpleNamespace(time=1)]

    data_orch.DataOrchestrator = DataOrchestrator
    monkeypatch.setitem(sys.modules, "openpine.data.orchestrator", data_orch)

    rt = types.ModuleType("openpine.runtime.engine")
    rt.load_strategy_class_from_artifact = lambda strategy_id: (object, SimpleNamespace(path="artifact", declaration_args={"x": 1}))

    class BacktestRunConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class BacktestEngineAdapter:
        def run(self, *a, **k):
            return SimpleNamespace(bars_processed=7)

    rt.BacktestRunConfig = BacktestRunConfig
    rt.BacktestEngineAdapter = BacktestEngineAdapter
    monkeypatch.setitem(sys.modules, "openpine.runtime.engine", rt)

    reg = Registry()
    async def _call(strategy_id, registry):
        response = await sr.strategy_replay(strategy_id, state=SimpleNamespace(), registry=registry)
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return response

    response = asyncio.run(_call("s1", reg))
    assert response["status"] == "started"
    assert ("s1", "running") in reg.statuses
    assert ("s1", "paused") in reg.statuses
    assert broadcasts[-1].status == "completed"

    # Error branch inside background replay.
    rt.load_strategy_class_from_artifact = lambda strategy_id: (_ for _ in ()).throw(RuntimeError("boom"))
    reg2 = Registry()
    response = asyncio.run(_call("s1", reg2))
    assert response["status"] == "started"
    assert ("s1", "error") in reg2.statuses
    assert broadcasts[-1].status == "failed"

    with pytest.raises(Exception):
        asyncio.run(_call("missing", Registry()))
