from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace


from openpine.gateway.routes import backtest as bt
from tests.admission_helpers import make_deployment_identity, make_sealed_artifact
from tests.rc4_fixtures import admitted_manifest, canonical_bar_envelopes, execution_context


class FakeWS:
    def __init__(self):
        self.events = []
    def update_progress(self, run_id, domain, status, progress, message, detail=None):
        self.events.append((run_id, status, progress, message, detail))
    async def broadcast_progress(self, run_id):
        self.events.append((run_id, "broadcast", None, None, None))


class FakeStore:
    def __init__(self):
        self.failed = []
        self.cancelled = []
        self.saved = []
    def mark_failed(self, run_id, message):
        self.failed.append((run_id, message))
    def mark_cancelled(self, run_id, message):
        self.cancelled.append((run_id, message))
    def save_result(self, **kwargs):
        self.saved.append(kwargs)


class FakeRegistry:
    def __init__(self, strategy=None, fail=False):
        self.strategy = strategy or SimpleNamespace(
            strategy_id="s1",
            pine_id="p1",
            artifact_id="a1",
            symbol="BTCUSDT",
            timeframe="1m",
            exchange="binance",
            market_type="spot",
            params_json='{"x": 1}',
            semantic_profile="strict_5x",
        )
        self.fail = fail
    def get_strategy(self, strategy_id):
        if self.fail:
            raise KeyError(strategy_id)
        return self.strategy


class FakeStorage:
    def __init__(self):
        self.sql = []
        self.has_col = False
    def execute(self, sql, params=()):
        self.sql.append((sql, params))
        if sql.startswith("PRAGMA"):
            rows = [(0, "run_id")] + ([(1, "data_fingerprint")] if self.has_col else [])
            return SimpleNamespace(fetchall=lambda: rows)
        return SimpleNamespace(fetchall=lambda: [])
    def commit(self):
        self.sql.append(("COMMIT", ()))


def _state(*, registry=None, orchestrator=None, storage=None, store=None):
    return SimpleNamespace(
        strategy_registry=registry or FakeRegistry(),
        backtest_store=store or FakeStore(),
        orchestrator=orchestrator or SimpleNamespace(load_bars=lambda query, progress_callback=None: SimpleNamespace(query=query, bars=[])),
        artifact_store=SimpleNamespace(
            get_artifact=lambda artifact_id, pine_id: make_sealed_artifact(
                {
                    "translation_metadata": {
                        "declaration": {
                            "arguments": {"commission_type": "cash_per_order"}
                        }
                    }
                }
            )
        ),
        storage=storage or FakeStorage(),
        backtest_cancel_requests=set(),
        config=SimpleNamespace(data_dir=Path(".openpine"), data_cache_root=None),
        admission_identity=make_deployment_identity(),
        admitted_manifest=admitted_manifest(),
    )


def test_backtest_background_strategy_and_artifact_failures(monkeypatch):
    ws = FakeWS()
    monkeypatch.setattr(bt, "ws_manager", ws)
    store = FakeStore()
    asyncio.run(bt._run_backtest_background(_state(registry=FakeRegistry(fail=True), store=store), "missing", "run1", 1, 2, None, 0, False))
    assert ws.events[-1][1] == "broadcast"
    assert store.failed == [("run1", "Strategy not found")]

    import openpine.runtime.engine as rt
    import openpine.runtime.isolated_run as isolated_run
    def bad_loader(*args, **kwargs):
        raise rt.BacktestArtifactError("bad artifact")
    monkeypatch.setattr(isolated_run, "capture_generated_source", bad_loader)
    store = FakeStore()
    state = _state(store=store)
    state.artifact_store = SimpleNamespace(get_artifact=bad_loader)
    asyncio.run(bt._run_backtest_background(state, "s1", "run2", 1, 2, None, 0, False))
    assert store.failed and "bad artifact" in store.failed[-1][1]
