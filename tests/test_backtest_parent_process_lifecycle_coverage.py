"""Backtest parent-process lifecycle and SQLite-backed route boundaries."""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import time
import uuid
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from openpine.gateway.routes import backtest
from openpine.storage.backtest_dto import BacktestRunRequest
from openpine.storage.backtest_storage import BacktestResultStore
from openpine.storage.migrations import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage
from tests.admission_helpers import make_deployment_identity, make_sealed_artifact
from tests.rc4_fixtures import admitted_manifest, canonical_series


def _request(strategy_id: str = "strategy-a") -> BacktestRunRequest:
    return BacktestRunRequest(
        strategy_id=strategy_id,
        pine_id="pine-a",
        artifact_id="artifact-a",
        params_hash="sha256:" + "b" * 64,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        from_time=0,
        to_time=120_000,
    )


@pytest.fixture
def sqlite_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage = SQLiteStorage(tmp_path / "backtest.sqlite")
    MigrationRunner().run_migrations(storage)
    import openpine.storage.backtest_storage as storage_module

    monkeypatch.setattr(
        storage_module.OpenPineConfig, "load", lambda: SimpleNamespace(data_dir=tmp_path)
    )
    store = BacktestResultStore(storage)
    state = SimpleNamespace(storage=storage, backtest_store=store)
    try:
        yield state
    finally:
        storage.close()


def test_idempotency_claim_lifecycle_uses_real_sqlite_and_reclaims_expired_rows(sqlite_state) -> None:
    state = sqlite_state
    first = backtest._claim_backtest_idempotency(state, "parent-process-key", "request-a")
    assert first.result_id is None and first.claim_token

    with pytest.raises(HTTPException, match="still being scheduled") as pending:
        backtest._claim_backtest_idempotency(state, "parent-process-key", "request-a")
    assert pending.value.status_code == 409

    with pytest.raises(HTTPException, match="different request") as mismatch:
        backtest._claim_backtest_idempotency(state, "parent-process-key", "request-b")
    assert mismatch.value.status_code == 409

    backtest._complete_backtest_idempotency(
        state, "parent-process-key", "request-a", first.claim_token, "run-persisted"
    )
    replay = backtest._claim_backtest_idempotency(
        state, "parent-process-key", "request-a"
    )
    assert replay.result_id == "run-persisted" and replay.claim_token is None

    now = int(time.time() * 1000)
    state.storage.execute(
        """
        INSERT INTO api_idempotency
        (scope, idempotency_key, request_hash, claim_token, result_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, NULL, ?, ?)
        """,
        ("backtest.run", "expired-pending", "old", "old-token", now, now - backtest._IDEMPOTENCY_PENDING_TTL_MS - 1),
    )
    state.storage.execute(
        """
        INSERT INTO api_idempotency
        (scope, idempotency_key, request_hash, claim_token, result_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("backtest.run", "expired-result", "old-result", "old-token", "run-old", now, now - backtest._IDEMPOTENCY_RESULT_TTL_MS - 1),
    )
    state.storage.commit()

    replacement = backtest._claim_backtest_idempotency(
        state, "expired-pending", "fresh-request"
    )
    assert replacement.claim_token is not None
    rows = state.storage.execute(
        "SELECT idempotency_key FROM api_idempotency WHERE scope = ? ORDER BY idempotency_key",
        ("backtest.run",),
    ).fetchall()
    assert [row[0] for row in rows] == ["expired-pending", "parent-process-key"]

    with pytest.raises(HTTPException, match="superseded") as superseded:
        backtest._complete_backtest_idempotency(
            state, "expired-pending", "fresh-request", "wrong-token", "run-never"
        )
    assert superseded.value.status_code == 409
    backtest._release_backtest_idempotency(
        state, "expired-pending", "fresh-request", replacement.claim_token
    )
    assert state.storage.execute(
        "SELECT count(*) FROM api_idempotency WHERE idempotency_key = ?", ("expired-pending",)
    ).fetchone()[0] == 0


def test_real_sqlite_routes_project_run_trade_report_and_delete(sqlite_state, tmp_path: Path) -> None:
    state = sqlite_state
    first_id = state.backtest_store.create_run(_request())
    second_id = state.backtest_store.create_run(_request())
    other_id = state.backtest_store.create_run(_request("strategy-b"))
    state.storage.execute(
        """
        INSERT INTO backtest_trades
        (trade_id, run_id, strategy_id, direction, entry_time, entry_price, qty,
         exit_time, exit_price, gross_pnl, net_pnl, bars_held, exit_reason, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        ("trade-1", second_id, "strategy-a", "long", 1, 100.0, 2.0, 2, 101.0, 2.0, 1.5, 1, "signal", 3),
    )
    report = state.backtest_store._data_dir / "strategy-a" / second_id / "report.md"
    report.parent.mkdir(parents=True)
    report.write_text("# independently persisted report\n", encoding="utf-8")
    state.storage.execute(
        """
        INSERT INTO backtest_artifacts
        (artifact_row_id, run_id, strategy_id, artifact_type, path, format, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("artifact-report", second_id, "strategy-a", "report_md", str(report), "markdown", 4),
    )
    state.storage.commit()
    state.strategy_registry = SimpleNamespace(
        get_strategy=lambda strategy_id: SimpleNamespace(name="Alpha")
        if strategy_id == "strategy-a"
        else (_ for _ in ()).throw(KeyError(strategy_id))
    )

    listed = asyncio.run(backtest.list_runs(state=state, limit=10))
    by_id = {row.run_id: row for row in listed}
    assert by_id[first_id].version == 1
    assert by_id[second_id].version == 2
    assert by_id[second_id].strategy_name == "Alpha"
    assert by_id[other_id].strategy_name is None

    detail = asyncio.run(backtest.get_run(second_id, state))
    assert detail.version == 2 and detail.strategy_name == "Alpha"
    trades = asyncio.run(backtest.get_run_trades(second_id, state, limit=10, offset=0))
    assert len(trades) == 1
    assert (trades[0].trade_id, trades[0].net_profit, trades[0].exit_reason) == ("trade-1", 1.5, "signal")
    report_response = asyncio.run(backtest.get_run_report(second_id, state))
    assert report_response == {
        "run_id": second_id,
        "format": "markdown",
        "data": "# independently persisted report\n",
    }
    exported = asyncio.run(backtest.export_run(second_id, state))
    assert exported["trades"] == [{
        "trade_id": "trade-1", "entry_time": 1, "exit_time": 2, "direction": "long",
        "entry_price": 100.0, "exit_price": 101.0, "stop_price": None,
        "take_profit_price": None, "qty": 2.0, "net_profit": 1.5,
    }]
    assert exported["artifacts"] == [{"type": "report_md", "filename": "report.md"}]

    asyncio.run(backtest.delete_run(first_id, state))
    assert state.backtest_store.get_run(first_id) is None
    with pytest.raises(HTTPException, match="Run not found") as missing:
        asyncio.run(backtest.get_run(first_id, state))
    assert missing.value.status_code == 404


def test_successful_background_lifecycle_admits_data_then_persists_terminal_sqlite_result(
    sqlite_state, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from marketdata_provider.contracts import (
        Bar,
        BarQuery,
        BarSeries,
        CoverageReport,
        InstrumentKey,
        parse_timeframe,
    )

    state = sqlite_state
    run_id = state.backtest_store.create_run(_request())
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe("1m")
    bars = (
        Bar(instrument, timeframe, 0, 60_000, 10.0, 12.0, 9.0, 11.0, 3.0, True),
        Bar(instrument, timeframe, 60_000, 120_000, 11.0, 13.0, 10.0, 12.0, 4.0, True),
    )
    query = BarQuery(instrument, timeframe, 0, 120_000, gap_policy="allow_with_metadata")
    series = canonical_series(BarSeries(query, bars, CoverageReport(0, 120_000, 0, 120_000)))
    events: list[tuple[str, str, float, str]] = []

    class Progress:
        def update_progress(self, run, _domain, status, pct, message, detail=None) -> None:
            assert run == run_id
            events.append((run, status, pct, message))

        async def broadcast_progress(self, run: str) -> None:
            assert run == run_id

    strategy = SimpleNamespace(
        strategy_id="strategy-a", pine_id="pine-a", artifact_id="artifact-a",
        params_hash="sha256:" + "b" * 64, exchange="binance", market_type="spot",
        symbol="BTCUSDT", timeframe="1m", params_json="{}", semantic_profile="strict_5x",
    )
    state.strategy_registry = SimpleNamespace(get_strategy=lambda _strategy_id: strategy)
    state.artifact_store = SimpleNamespace(get_artifact=lambda *_args: make_sealed_artifact())
    state.orchestrator = SimpleNamespace(
        load_bars=lambda _query, *, progress_callback: (
            progress_callback(2, 1, 2, 1, 0, "provider"), series
        )[1]
    )
    state.backtest_cancel_requests = set()
    state.config = SimpleNamespace(data_dir=tmp_path, data_cache_root=None)
    state.admission_identity = make_deployment_identity()
    state.admitted_manifest = admitted_manifest()
    monkeypatch.setattr(backtest, "ws_manager", Progress())
    monkeypatch.setattr(
        backtest,
        "_run_owned_backtest",
        lambda *_args: SimpleNamespace(
            bars_processed=2,
            raw_result=SimpleNamespace(trades=[], equity_curve=None, plots=None),
        ),
    )

    asyncio.run(
        backtest._run_backtest_background(
            state, "strategy-a", run_id, 0, 120_000, None, 0, False
        )
    )

    row = state.storage.execute(
        "SELECT status, data_fingerprint FROM backtest_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert row[0] == "done"
    assert isinstance(row[1], str) and len(row[1]) == 64
    assert [status for _, status, _, _ in events][-1] == "completed"
    assert events[-1][2] == 1.0


def test_parent_subprocess_reaps_only_its_actual_descendants() -> None:
    script = """
import json
import os
import subprocess
import sys
import time
coverage = None
coverage_path = os.environ.get('OPENPINE_CHILD_COVERAGE')
from multiprocessing import get_context
from openpine.gateway.routes.backtest import _descendant_process_identities, _supervised_backtest_process_entry, _terminate_current_process_descendants
if coverage_path:
    from coverage import Coverage
    coverage = Coverage(data_file=coverage_path, branch=True, source=['openpine.gateway.routes.backtest'])
    coverage.start()
try:
    ctx = get_context('fork')
    def output_channel():
        receiver, sender = ctx.Pipe(duplex=False)
        class Output:
            def put(self, value):
                sender.send(value)
            def close(self):
                sender.close()
            def join_thread(self):
                return None
        return receiver, Output()

    supervisor_receiver, supervisor_queue = output_channel()
    supervisor_cleanup = ctx.Event()
    def return_from_supervised_child(out):
        out.put(('ok', 'supervised-child-result'))
    _supervised_backtest_process_entry(
        supervisor_queue, return_from_supervised_child, (), supervisor_cleanup
    )
    assert supervisor_cleanup.is_set()
    assert supervisor_receiver.recv() == ('ok', 'supervised-child-result')

    children = [subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)']) for _ in range(2)]
    before = _descendant_process_identities(os.getpid())
    assert all(child.pid in before for child in children), before
    _terminate_current_process_descendants(timeout=4.0)
    print(json.dumps({'before': sorted(before), 'codes': [child.wait(timeout=2) for child in children], 'after': _descendant_process_identities(os.getpid())}))
finally:
    if coverage:
        coverage.stop()
        coverage.save()
"""
    completed = subprocess.run(  # noqa: S603 -- fixed interpreter and test-owned script
        [sys.executable, "-c", script],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    outcome = json.loads(completed.stdout)
    assert len(outcome["before"]) >= 2
    assert all(code != 0 for code in outcome["codes"])
    assert outcome["after"] == {}


def test_admission_lease_is_held_until_owned_worker_unregisters() -> None:
    run_id = f"lease-{uuid.uuid4().hex}"
    limiter = backtest._BacktestAdmissionLimiter(1)
    lease = limiter.try_acquire()
    assert lease is not None
    worker = backtest._BacktestWorker(
        process=SimpleNamespace(pid=None, is_alive=lambda: False, join=lambda _timeout: None),
        out=SimpleNamespace(),
        process_group=None,
        start_time=None,
    )
    try:
        assert backtest._admit_backtest_worker_start(run_id, set(), worker)
        assert backtest._retain_backtest_admission_lease(run_id, lease)
        assert limiter.try_acquire() is None
        backtest._register_backtest_worker(run_id, worker)
        backtest._set_backtest_worker_starting(run_id, False)
        workers, sealed = backtest._seal_backtest_terminal_if_quiescent(run_id)
        assert workers == (worker,) and sealed is False
        backtest._unregister_backtest_worker(run_id, worker)
        replacement = limiter.try_acquire()
        assert replacement is not None
        replacement.release()
    finally:
        backtest._set_backtest_worker_starting(run_id, False)
        backtest._unregister_backtest_worker(run_id, worker)
        with backtest._ACTIVE_BACKTEST_WORKERS_LOCK:
            backtest._TERMINAL_BACKTEST_RUNS.discard(run_id)
            backtest._TERMINAL_BACKTEST_OUTCOMES.pop(run_id, None)
            backtest._RETAINED_BACKTEST_LEASES.pop(run_id, None)
        lease.release()
