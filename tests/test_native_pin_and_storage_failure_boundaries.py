"""Native pin ownership and unavailable SQLite fail before publishing completion."""

from types import SimpleNamespace
import errno
import os
import subprocess
import sys
import pytest
from fastapi import HTTPException
from openpine.gateway.routes import backtest as b
from openpine.storage.sqlite_storage import SQLiteStorage
from openpine.storage.migrations import MigrationRunner


@pytest.mark.parametrize("operation", ["complete", "release"])
def test_idempotency_write_on_closed_real_sqlite_is_unavailable(tmp_path, operation):
    storage = SQLiteStorage(tmp_path / "identity.sqlite")
    MigrationRunner().run_migrations(storage)
    storage.close()
    state = SimpleNamespace(storage=storage)
    with pytest.raises(HTTPException) as caught:
        if operation == "complete":
            b._complete_backtest_idempotency(state, "key", "request", "token", "run")
        else:
            b._release_backtest_idempotency(state, "key", "request", "token")
    assert (
        caught.value.status_code == 503
        and caught.value.detail == "Backtest idempotency storage is unavailable"
    )


@pytest.mark.parametrize("after", ["missing", "zombie", "birth-changed", "read-failed"])
def test_native_pidfd_is_closed_when_post_open_identity_does_not_match(monkeypatch, after):
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(30)"])  # noqa: S603 -- fixed test-owned child
    descriptors = []
    actual_open = b._pidfd_open
    identity = b._proc_identity(child.pid)
    assert identity is not None
    worker = b._BacktestWorker(child, SimpleNamespace(), None, identity[2])
    readings = [0]

    def observe(pid):
        readings[0] += 1
        if readings[0] == 1:
            return identity
        if after == "read-failed":
            raise OSError("post-open identity unavailable")
        if after == "missing":
            return None
        return (
            "Z" if after == "zombie" else identity[0],
            identity[1],
            identity[2] + (1 if after == "birth-changed" else 0),
        )

    def pin(pid):
        fd = actual_open(pid)
        descriptors.append(fd)
        return fd

    monkeypatch.setattr(b, "_proc_identity", observe)
    monkeypatch.setattr(b, "_pidfd_open", pin)
    try:
        if after == "read-failed":
            with pytest.raises(OSError):
                b._pin_backtest_worker(worker)
        else:
            assert b._pin_backtest_worker(worker) is None
        assert child.poll() is None and len(descriptors) == 1
        with pytest.raises(OSError) as caught:
            os.fstat(descriptors[0])
        assert caught.value.errno == errno.EBADF
    finally:
        child.kill()
        child.wait(timeout=3)


@pytest.mark.parametrize("failure", ["result", "error"])
def test_callable_cleanup_failure_never_emits_success(monkeypatch, failure):
    rows = []
    monkeypatch.setattr(
        b,
        "_terminate_current_process_descendants",
        lambda: (_ for _ in ()).throw(RuntimeError("owned cleanup unverified")),
    )
    out = SimpleNamespace(put=lambda row: rows.append(row))
    if failure == "result":
        b._put_backtest_process_result(out, object())
    else:
        b._put_backtest_process_error(out, ValueError("callable failed"))
    assert len(rows) == 1 and rows[0][0] == "err" and rows[0][1] == "RuntimeError"
    assert "cleanup" in rows[0][2] and not any(row[0] == "ok" for row in rows)


@pytest.mark.parametrize("failure", ["close", "cancel-join", "both"])
def test_queue_cleanup_reports_first_failure_but_attempts_both_operations(failure):
    events = []

    def close():
        events.append("close")
        if failure in {"close", "both"}:
            raise OSError("queue close refused")

    def cancel():
        events.append("cancel")
        if failure in {"cancel-join", "both"}:
            raise ValueError("queue cancellation refused")

    with pytest.raises((OSError, ValueError)) as caught:
        b._close_backtest_queue(SimpleNamespace(close=close, cancel_join_thread=cancel))
    assert events == ["close", "cancel"]
    assert str(caught.value) == (
        "queue cancellation refused" if failure == "cancel-join" else "queue close refused"
    )


@pytest.mark.parametrize("exit_first", [False, True])
def test_native_join_fallback_waits_only_owned_child(exit_first):
    child = subprocess.Popen([sys.executable, "-c", "import time;time.sleep(.03)"])  # noqa: S603 -- fixed test-owned child
    worker = b._BacktestWorker(
        SimpleNamespace(
            join=lambda timeout: (_ for _ in ()).throw(
                AssertionError("multiprocessing state unavailable")
            )
        ),
        SimpleNamespace(),
        None,
        None,
        owned_pid=child.pid,
    )
    try:
        b._join_backtest_worker(worker, 0.001)
        if exit_first:
            child.wait(timeout=3)
        b._join_backtest_worker(worker, 0.2)
        assert child.poll() is not None
    finally:
        if child.poll() is None:
            child.kill()
        child.wait(timeout=3)
