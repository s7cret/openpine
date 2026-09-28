"""Backtest background failures persist truthful terminal state in real SQLite."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.gateway.routes import backtest
from openpine.storage.backtest_dto import BacktestRunRequest
from openpine.storage.backtest_storage import BacktestResultStore
from openpine.storage.migrations import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage
from tests.admission_helpers import make_sealed_artifact
from tests.test_backend_coverage_pass31 import FakeRegistry, FakeWS


@pytest.fixture(scope="module")
def sealed_artifact() -> dict:
    return make_sealed_artifact()


@pytest.fixture
def backed_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage = SQLiteStorage(tmp_path / "gateway.sqlite")
    MigrationRunner().run_migrations(storage)
    import openpine.storage.backtest_storage as module
    monkeypatch.setattr(module.OpenPineConfig, "load", lambda: SimpleNamespace(data_dir=tmp_path))
    store = BacktestResultStore(storage)
    record = BacktestRunRequest(
        strategy_id="s1", pine_id="p1", artifact_id="a1", params_hash="sha256:" + "a" * 64,
        symbol="BTCUSDT", timeframe="1m", exchange="binance", market_type="spot",
        from_time=0, to_time=60_000,
    )
    run_id = store.create_run(record)
    ws = FakeWS()
    monkeypatch.setattr(backtest, "ws_manager", ws)
    state = SimpleNamespace(
        strategy_registry=FakeRegistry(), backtest_store=store,
        artifact_store=SimpleNamespace(get_artifact=lambda *_: (_ for _ in ()).throw(FileNotFoundError("missing sealed artifact"))),
        orchestrator=SimpleNamespace(load_bars=lambda *_args, **_kwargs: SimpleNamespace(bars=[])),
        backtest_cancel_requests=set(),
    )
    try:
        yield state, run_id, ws, storage
    finally:
        storage.close()


def _execute(state: SimpleNamespace, run_id: str) -> None:
    asyncio.run(backtest._run_backtest_background(
        state, "s1", run_id, 0, 60_000, None, 0, False,
    ))


def test_missing_sealed_artifact_is_persisted_as_failure(backed_state) -> None:
    state, run_id, ws, storage = backed_state
    _execute(state, run_id)
    row = storage.execute("SELECT status,error_message FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    assert tuple(row) == ("failed", "missing sealed artifact")
    assert any(event[1] == "failed" and "missing sealed artifact" in event[3] for event in ws.events)
    assert run_id not in state.backtest_cancel_requests


def test_cancel_before_artifact_access_persists_only_cancelled(backed_state) -> None:
    state, run_id, ws, storage = backed_state
    state.backtest_cancel_requests.add(run_id)
    _execute(state, run_id)
    row = storage.execute("SELECT status,error_message FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    assert tuple(row) == ("cancelled", "Cancelled during strategy load")
    assert run_id not in state.backtest_cancel_requests
    assert any(event[1] == "cancelled" for event in ws.events)
    assert not any(event[1] == "failed" for event in ws.events)


@pytest.mark.parametrize("load_error,expected", [
    (RuntimeError("provider page read failed"), "Data load failed: provider page read failed"),
    (None, "No bars found in range"),
])
def test_marketdata_failure_never_publishes_empty_success(
    backed_state, sealed_artifact: dict, load_error: Exception | None, expected: str,
) -> None:
    state, run_id, ws, storage = backed_state
    state.artifact_store = SimpleNamespace(get_artifact=lambda *_: sealed_artifact)
    if load_error is not None:
        def load_bars(*_args, **_kwargs):
            raise load_error
        state.orchestrator = SimpleNamespace(load_bars=load_bars)
    _execute(state, run_id)
    row = storage.execute("SELECT status,error_message FROM backtest_runs WHERE run_id=?", (run_id,)).fetchone()
    assert tuple(row) == ("failed", expected)
    assert any(event[1] == "failed" for event in ws.events)
    assert not any(event[1] == "completed" for event in ws.events)
