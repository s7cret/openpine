from __future__ import annotations

import asyncio
import sys
import types
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.gateway.routes import strategies as sr
from tests.admission_helpers import make_sealed_artifact
from tests.rc4_fixtures import HASH_A, HASH_B, admitted_manifest


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


def test_strategy_replay_success_and_failure(monkeypatch, job_store):
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
        def load_bars(self, query):
            bar = SimpleNamespace(
                time=1,
                time_close=60_000,
                open=1,
                high=1,
                low=1,
                close=1,
                volume=1,
            )
            return SimpleNamespace(
                bars=[bar],
                canonical_bars=[
                    {
                        "series_id": "binance/spot/BTCUSDT:1m",
                        "instrument_id": "binance/spot/BTCUSDT",
                    }
                ],
            )

    data_orch.DataOrchestrator = DataOrchestrator
    monkeypatch.setitem(sys.modules, "openpine.data.orchestrator", data_orch)

    rt = types.ModuleType("openpine.runtime.engine")
    rt.capture_generated_source = lambda *a, **k: b"src"

    class BacktestRunConfig:
        def __init__(self, **kw):
            self.__dict__.update(kw)

    class BacktestEngineAdapter:
        def run_isolated(self, *a, **k):
            return SimpleNamespace(bars_processed=7)

    rt.BacktestRunConfig = BacktestRunConfig
    rt.BacktestEngineAdapter = BacktestEngineAdapter
    monkeypatch.setitem(sys.modules, "openpine.runtime.engine", rt)

    iso = types.ModuleType("openpine.runtime.isolated_run")
    iso.capture_generated_source = lambda *a, **k: b"src"
    monkeypatch.setitem(sys.modules, "openpine.runtime.isolated_run", iso)

    reg = Registry()
    from openpine.gateway.routes import backtest as backtest_routes

    monkeypatch.setattr(
        backtest_routes,
        "_admit_loaded_backtest_run",
        lambda *a, **k: {"data_snapshot_hash": HASH_A, "content_hash": HASH_B},
    )

    async def _call(strategy_id, registry):
        artifact_store = SimpleNamespace(
            get_artifact=lambda *a, **k: make_sealed_artifact(python_code="src")
        )
        response = await sr.strategy_replay(
            strategy_id,
            state=SimpleNamespace(
                orchestrator=DataOrchestrator(),
                artifact_store=artifact_store,
                job_store=job_store,
                config=SimpleNamespace(data_dir=Path(".openpine")),
                admitted_manifest=admitted_manifest(),
            ),
            registry=registry,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return response

    response = asyncio.run(_call("s1", reg))
    assert response["status"] == "started"
    assert ("s1", "running") in reg.statuses
    assert ("s1", "paused") in reg.statuses
    assert broadcasts[-1].status == "completed"

    # Error branch inside background replay.
    class BrokenStore:
        def get_artifact(self, *args, **kwargs):
            raise RuntimeError("boom")

    reg2 = Registry()
    async def _broken_call():
        response = await sr.strategy_replay(
            "s1",
            state=SimpleNamespace(
                orchestrator=DataOrchestrator(),
                artifact_store=BrokenStore(),
                job_store=job_store,
                config=SimpleNamespace(data_dir=Path(".openpine")),
            ),
            registry=reg2,
        )
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        return response

    response = asyncio.run(_broken_call())
    assert response["status"] == "started"
    assert ("s1", "error") in reg2.statuses
    assert broadcasts[-1].status == "failed"

    with pytest.raises(Exception):
        asyncio.run(_call("missing", Registry()))
