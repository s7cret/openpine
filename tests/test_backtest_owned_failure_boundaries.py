"""Owned process failures and real SQLite terminal outcomes preserve admission."""

from tests.test_backtest_route_background_rc6_coverage import (
    sqlite_backtest_state as sqlite_backtest_state,
)

from types import SimpleNamespace
import asyncio
import os
import subprocess
import sys
import uuid
import pytest
from openpine.gateway.routes import backtest as b
from tests.test_backtest_route_background_rc6_coverage import _canonical_series, _Progress
from tests.admission_helpers import make_sealed_artifact


@pytest.mark.parametrize(
    "payload",
    [(True, 1), (1, True), (False, 1), (1, False), (1,), [1, 2], (0, 1), (1, -1), ("1", 1)],
)
def test_startup_channel_refuses_boolean_or_malformed_process_identity(payload):
    closed = []
    receiver = SimpleNamespace(recv=lambda: payload, close=lambda: closed.append(True))
    with pytest.raises(RuntimeError, match="invalid backtest supervisor startup identity"):
        b._receive_backtest_startup_identity(receiver)
    assert closed == [True]


@pytest.mark.parametrize(
    "failure",
    [
        "compute-error",
        "compute-cancel",
        "save-error",
        "missing-canonical",
        "missing-deployment",
        "missing-manifest",
    ],
)
def test_background_failure_never_persists_success_in_real_sqlite(
    sqlite_backtest_state, monkeypatch, failure
):
    state, run, storage = sqlite_backtest_state
    state.artifact_store = SimpleNamespace(get_artifact=lambda *a: make_sealed_artifact())
    state.orchestrator = SimpleNamespace(
        load_bars=lambda *a, **k: [] if failure == "missing-canonical" else _canonical_series()
    )
    if failure == "missing-deployment":
        state.admission_identity = None
    if failure == "missing-manifest":
        state.admitted_manifest = None
    progress = _Progress()
    monkeypatch.setattr(b, "ws_manager", progress)

    def compute(*args):
        args[-1](1, 2)
        if failure == "compute-cancel":
            raise b._BacktestCancelled("owned compute cancelled")
        if failure == "compute-error":
            raise RuntimeError("owned compute failed")
        return SimpleNamespace(
            bars_processed=2, raw_result=SimpleNamespace(trades=[], equity_curve=None, plots=None)
        )

    monkeypatch.setattr(b, "_run_owned_backtest", compute)
    if failure == "save-error":
        monkeypatch.setattr(
            state.backtest_store,
            "save_result",
            lambda **k: (_ for _ in ()).throw(OSError("storage failed")),
        )
    try:
        asyncio.run(b._run_backtest_background(state, "strategy-1", run, 0, 120000, None, 0, False))
        row = storage.execute(
            "SELECT status,error_message FROM backtest_runs WHERE run_id=?", (run,)
        ).fetchone()
        assert row[0] == ("cancelled" if failure == "compute-cancel" else "failed")
        assert not any(e["status"] == "completed" for e in progress.events)
    finally:
        with b._ACTIVE_BACKTEST_WORKERS_LOCK:
            b._TERMINAL_BACKTEST_RUNS.discard(run)
            b._TERMINAL_BACKTEST_OUTCOMES.pop(run, None)


@pytest.mark.parametrize("failure", ["foreign-worker", "different-lease", "terminal-won"])
def test_registration_conflict_cannot_release_other_owner_permit(failure):
    run = "unit-" + uuid.uuid4().hex
    limiter = b._BacktestAdmissionLimiter(2)
    first = limiter.try_acquire()
    second = limiter.try_acquire()
    worker = b._BacktestWorker(SimpleNamespace(pid=None), SimpleNamespace(), None, None)
    other = b._BacktestWorker(SimpleNamespace(pid=None), SimpleNamespace(), None, None)
    try:
        assert b._admit_backtest_worker_start(run, set(), worker)
        assert b._retain_backtest_admission_lease(run, first)
        if failure == "foreign-worker":
            with pytest.raises(RuntimeError, match="already registered"):
                b._register_backtest_worker(run, other)
        elif failure == "different-lease":
            with pytest.raises(RuntimeError, match="already retained"):
                b._retain_backtest_admission_lease(run, second)
        else:
            assert b._seal_backtest_failure(run)
            assert not b._seal_backtest_failure(run)
            assert b._backtest_terminal_outcome(run) == "failed"
        assert limiter.try_acquire() is None
        b._unregister_backtest_worker(run, other)
        assert limiter.try_acquire() is None
    finally:
        b._set_backtest_worker_starting(run, False)
        b._unregister_backtest_worker(run, worker)
        first.release()
        second.release()
        with b._ACTIVE_BACKTEST_WORKERS_LOCK:
            b._TERMINAL_BACKTEST_RUNS.discard(run)
            b._TERMINAL_BACKTEST_OUTCOMES.pop(run, None)


@pytest.mark.parametrize(
    "failure",
    ["setsid", "fork", "target-exception", "abrupt-exit", "cleanup-error", "output-error"],
)
def test_real_supervisor_child_retains_failed_terminal_receipt(failure):
    # All changes are confined to a separate native process and its own children.
    script = r"""
import json,os,sys
from multiprocessing import get_context
from openpine.gateway.routes import backtest as b
case=sys.argv[1];ctx=get_context('fork');receiver,sender=ctx.Pipe(duplex=False);complete=ctx.Event()
class Output:
 def put(self,value):
  if case=='output-error':raise OSError('output refused')
  sender.send(value)
 def close(self):sender.close()
 def join_thread(self):pass
out=Output()
def target(out):
 if case=='abrupt-exit':os._exit(13)
 raise ValueError('owned callable failure')
if case=='setsid':b.os.setsid=lambda:(_ for _ in ()).throw(OSError('setsid refused'))
if case=='fork':b.os.fork=lambda:(_ for _ in ()).throw(OSError('fork refused'))
if case=='cleanup-error':b._terminate_current_process_descendants=lambda **k:(_ for _ in ()).throw(RuntimeError('cleanup unverified'))
try:b._supervised_backtest_process_entry(out,target,(),complete)
except RuntimeError as e:assert case=='cleanup-error' and 'cleanup failed' in str(e)
try:row=receiver.recv()
except EOFError:row=None
assert row is None if case=='output-error' else row[0]=='err'
assert complete.is_set()==(case!='cleanup-error')
print(json.dumps({'case':case,'cleanup_complete':complete.is_set(),'error':None if row is None else row[1:3]}))
"""
    completed = subprocess.run(  # noqa: S603 -- fixed interpreter and test-owned script, enum fault input
        [sys.executable, "-c", script, failure],
        env=os.environ.copy(),
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert failure in completed.stdout
