from __future__ import annotations

import asyncio
import multiprocessing as mp
import os
import signal
import threading
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from openpine.gateway.routes import backtest


def _abrupt_entry_with_detached_child(_out, pid_path: str, marker_path: str) -> None:
    child_pid = os.fork()
    if child_pid == 0:
        os.setsid()
        Path(pid_path).write_text(str(os.getpid()), encoding="utf-8")
        time.sleep(0.6)
        Path(marker_path).write_text("escaped", encoding="utf-8")
        os._exit(0)
    os._exit(7)


def _successful_entry(out, value: str) -> None:
    out.put(("ok", value))


class _Queue:
    def __init__(self, process=None) -> None:
        self.process = process
        self.closed = False
        self.cancelled = False

    def close(self) -> None:
        self.closed = True

    def cancel_join_thread(self) -> None:
        self.cancelled = True

    def get(self, timeout=None):
        del timeout
        return ("ok", "result")

    def get_nowait(self):
        raise AssertionError("unexpected queue drain")

class _Context:
    def __init__(self, process) -> None:
        self.process = process
        self.queue = _Queue(process)

    def Queue(self):
        return self.queue

    def Process(self, *, target, args):
        del target, args
        return self.process


class _PartialStartProcess:
    pid = None

    def __init__(self) -> None:
        self.alive = False

    def start(self) -> None:
        self.pid = 4242
        self.alive = True
        raise RuntimeError("partial start")

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout=None) -> None:
        del timeout


class _Process:
    def __init__(self, pid: int = 4242) -> None:
        self.pid = pid
        self.alive = False
        self.exitcode = 0
        self.terminated = 0
        self.killed = 0

    def start(self) -> None:
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout=None) -> None:
        del timeout

    def terminate(self) -> None:
        self.terminated += 1

    def kill(self) -> None:
        self.killed += 1


def test_queue_cancel_join_runs_when_close_raises():
    class BrokenCloseQueue(_Queue):
        def close(self) -> None:
            self.closed = True
            raise RuntimeError("close failed")

    out = BrokenCloseQueue()
    with pytest.raises(RuntimeError, match="close failed"):
        backtest._close_backtest_queue(out)
    assert out.closed is True
    assert out.cancelled is True


def test_linux_subreaper_setup_fails_closed_without_procfs(monkeypatch):
    monkeypatch.setattr(
        backtest, "sys", type("Sys", (), {"platform": "linux"}), raising=False
    )
    monkeypatch.setattr(backtest.Path, "exists", lambda _path: False)

    with pytest.raises(RuntimeError, match="procfs"):
        backtest._enable_child_subreaper()


@pytest.mark.parametrize(
    ("failure", "message"),
    [
        (PermissionError("denied"), "unreadable process identity"),
        (None, "malformed process identity"),
    ],
)
def test_process_identity_errors_fail_closed(monkeypatch, failure, message):
    def read_text(_path, **_kwargs):
        if failure is not None:
            raise failure
        return "malformed"

    monkeypatch.setattr(backtest.Path, "read_text", read_text)

    with pytest.raises(RuntimeError, match=message):
        backtest._proc_identity(4242)


def test_worker_termination_uses_pinned_pidfd_not_numeric_process_methods(monkeypatch):
    process = _Process(pid=100)
    process.alive = True
    cleanup_complete = threading.Event()
    worker = backtest._BacktestWorker(
        process=process,
        out=object(),
        process_group=100,
        start_time=11,
        cleanup_complete=cleanup_complete,
    )
    signals = []
    closes = []

    process.terminate = lambda: (_ for _ in ()).throw(
        AssertionError("numeric Process.terminate is forbidden")
    )
    process.kill = lambda: (_ for _ in ()).throw(
        AssertionError("numeric Process.kill is forbidden")
    )
    monkeypatch.setattr(
        backtest,
        "_proc_identity",
        lambda pid: ("S", 100, 11) if pid == 100 and process.alive else None,
    )
    monkeypatch.setattr(backtest, "_pidfd_open", lambda pid: 77 if pid == 100 else -1)
    monkeypatch.setattr(backtest.os, "close", lambda descriptor: closes.append(descriptor))

    def send_signal(descriptor, sig):
        assert descriptor == 77
        signals.append(sig)
        if sig == signal.SIGTERM:
            process.alive = False
            cleanup_complete.set()

    monkeypatch.setattr(backtest, "_pidfd_send_signal", send_signal)
    monkeypatch.setattr(backtest, "_pidfd_has_exited", lambda descriptor: not process.alive)

    assert backtest._terminate_backtest_worker(worker, timeout=0.1) is True
    assert signals == [signal.SIGTERM]
    assert closes == [77]


def test_descendant_cleanup_freezes_before_termination_and_observes_exit(monkeypatch):
    alive = {200}
    frozen = set()
    signals = []
    exit_polls = []

    monkeypatch.setattr(
        backtest,
        "_descendant_process_identities",
        lambda _root: {pid: 11 for pid in alive},
    )
    root_pid = os.getpid()
    monkeypatch.setattr(
        backtest,
        "_proc_identity",
        lambda pid: (
            ("S", 1, 11) if pid in alive
            else ("S", root_pid, 99) if pid == root_pid
            else None
        ),
    )
    monkeypatch.setattr(backtest, "_pidfd_open", lambda pid: pid + 1000)
    monkeypatch.setattr(backtest.os, "close", lambda _descriptor: None)

    def send_signal(descriptor, sig):
        pid = descriptor - 1000
        signals.append((pid, sig))
        if sig == signal.SIGSTOP:
            frozen.add(pid)
        elif sig == signal.SIGKILL:
            assert pid in frozen
            alive.discard(pid)

    def has_exited(descriptor):
        pid = descriptor - 1000
        exit_polls.append(pid)
        return pid not in alive

    monkeypatch.setattr(backtest, "_pidfd_send_signal", send_signal)
    monkeypatch.setattr(backtest, "_pidfd_has_exited", has_exited)
    monkeypatch.setattr(
        backtest,
        "_process_tasks_are_stopped",
        lambda pid, start: pid in frozen,
        raising=False,
    )

    backtest._terminate_current_process_descendants(timeout=0.2)

    assert signals[0] == (200, signal.SIGSTOP)
    assert (200, signal.SIGKILL) in signals
    assert 200 in exit_polls


def test_descendant_pidfd_open_failure_preserves_supervisor_until_retry(monkeypatch):
    root_pid = os.getpid()
    alive = {200}
    frozen = set()
    allow_pin = threading.Event()
    retry_observed = threading.Event()
    errors: list[BaseException] = []

    monkeypatch.setattr(
        backtest,
        "_descendant_process_identities",
        lambda _root: {200: 11} if 200 in alive else {},
    )
    monkeypatch.setattr(
        backtest,
        "_proc_identity",
        lambda pid: (
            ("S", 1, 11) if pid == 200 and pid in alive
            else ("S", root_pid, 99) if pid == root_pid
            else None
        ),
    )

    def pidfd_open(pid):
        assert pid == 200
        if not allow_pin.is_set():
            retry_observed.set()
            raise OSError("injected pidfd acquisition failure")
        return 1200

    def send_signal(_descriptor, sig):
        if sig == signal.SIGSTOP:
            frozen.add(200)
        elif sig == signal.SIGKILL:
            assert 200 in frozen
            alive.discard(200)

    monkeypatch.setattr(backtest, "_pidfd_open", pidfd_open)
    monkeypatch.setattr(backtest, "_pidfd_send_signal", send_signal)
    monkeypatch.setattr(
        backtest, "_pidfd_has_exited", lambda _descriptor: 200 not in alive
    )
    monkeypatch.setattr(
        backtest,
        "_process_tasks_are_stopped",
        lambda _pid, _start: 200 in frozen,
    )
    monkeypatch.setattr(backtest.os, "close", lambda _descriptor: None)

    def cleanup() -> None:
        try:
            backtest._terminate_current_process_descendants(timeout=0.2)
        except BaseException as exc:
            errors.append(exc)

    thread = threading.Thread(target=cleanup)
    thread.start()
    assert retry_observed.wait(timeout=0.2)
    thread.join(timeout=0.03)
    assert thread.is_alive(), "supervisor boundary exited while descendant was unpinned"

    allow_pin.set()
    thread.join(timeout=0.5)
    assert not thread.is_alive()
    assert errors == []
    assert alive == set()


def test_descendant_cleanup_rescans_frozen_tree_to_fixed_point(monkeypatch):
    alive = {200, 300}
    frozen = set()
    pinned = []

    def descendants(_root):
        if 200 not in alive:
            return {300: 22} if 200 in frozen and 300 in alive else {}
        visible = {200: 11}
        if 200 in frozen and 300 in alive:
            visible[300] = 22
        return visible

    monkeypatch.setattr(backtest, "_descendant_process_identities", descendants)
    root_pid = os.getpid()
    monkeypatch.setattr(
        backtest,
        "_proc_identity",
        lambda pid: (
            ("S", 1, 11) if pid == 200 and pid in alive
            else ("S", 200, 22) if pid == 300 and pid in alive
            else ("S", root_pid, 99) if pid == root_pid
            else None
        ),
    )

    def pidfd_open(pid):
        pinned.append(pid)
        return pid + 1000

    def send_signal(descriptor, sig):
        pid = descriptor - 1000
        if sig == signal.SIGSTOP:
            frozen.add(pid)
        elif sig == signal.SIGKILL:
            assert pid in frozen
            alive.discard(pid)

    monkeypatch.setattr(backtest, "_pidfd_open", pidfd_open)
    monkeypatch.setattr(backtest, "_pidfd_send_signal", send_signal)
    monkeypatch.setattr(
        backtest,
        "_pidfd_has_exited",
        lambda descriptor: descriptor - 1000 not in alive,
    )
    monkeypatch.setattr(
        backtest,
        "_process_tasks_are_stopped",
        lambda pid, start: pid in frozen,
        raising=False,
    )
    monkeypatch.setattr(backtest.os, "close", lambda _descriptor: None)

    backtest._terminate_current_process_descendants(timeout=0.2)

    assert pinned == [200, 300]
    assert alive == set()


def test_descendant_cleanup_error_waits_for_owned_pidfd_exit(monkeypatch):
    root_pid = os.getpid()
    alive = {200}
    killed = set()
    polls_after_kill = []

    monkeypatch.setattr(
        backtest,
        "_descendant_process_identities",
        lambda _root: {200: 11} if 200 in alive else {},
    )
    monkeypatch.setattr(
        backtest,
        "_proc_identity",
        lambda pid: (
            ("S", 1, 11) if pid == 200 and pid in alive
            else ("S", root_pid, 99) if pid == root_pid
            else None
        ),
    )
    monkeypatch.setattr(backtest, "_pidfd_open", lambda _pid: 1200)
    monkeypatch.setattr(backtest.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(
        backtest,
        "_process_tasks_are_stopped",
        lambda _pid, _start: (_ for _ in ()).throw(RuntimeError("stop check failed")),
    )

    def send_signal(_descriptor, sig):
        if sig == signal.SIGKILL:
            killed.add(200)
            alive.discard(200)

    def has_exited(_descriptor):
        if 200 in killed:
            polls_after_kill.append(200)
        return 200 not in alive

    monkeypatch.setattr(backtest, "_pidfd_send_signal", send_signal)
    monkeypatch.setattr(backtest, "_pidfd_has_exited", has_exited)

    with pytest.raises(RuntimeError, match="stop check failed"):
        backtest._terminate_current_process_descendants(timeout=0.2)

    assert polls_after_kill == [200]


def test_failed_cleanup_proof_keeps_worker_registered_for_retry(monkeypatch):
    run_id = "cleanup-proof-retained"
    process = _Process()
    context = _Context(process)
    monkeypatch.setattr(backtest.mp, "get_context", lambda _name: context)
    monkeypatch.setattr(
        backtest, "_proc_identity", lambda _pid: ("S", process.pid, 7)
    )
    monkeypatch.setattr(backtest, "_terminate_backtest_worker", lambda _worker: False)

    try:
        with pytest.raises(RuntimeError, match="cleanup deadline"):
            backtest._execute_backtest_process(run_id, set(), object(), ())
        retained = backtest._active_backtest_worker(run_id)
        assert retained is not None
        assert retained.process is process
    finally:
        retained = backtest._active_backtest_worker(run_id)
        if retained is not None:
            backtest._unregister_backtest_worker(run_id, retained)


def test_process_constructor_failure_closes_queue(monkeypatch):
    process = _Process()
    context = _Context(process)
    context.Process = lambda **_kwargs: (_ for _ in ()).throw(
        RuntimeError("constructor failed")
    )
    monkeypatch.setattr(backtest.mp, "get_context", lambda _name: context)

    with pytest.raises(RuntimeError, match="constructor failed"):
        backtest._execute_backtest_process("constructor", set(), object(), ())

    assert context.queue.closed is True
    assert context.queue.cancelled is True
    assert backtest._backtest_worker_is_starting("constructor") is False


def test_partial_process_start_is_reaped_before_error_returns(monkeypatch):
    process = _PartialStartProcess()
    context = _Context(process)
    terminated = []
    monkeypatch.setattr(backtest.mp, "get_context", lambda _name: context)
    monkeypatch.setattr(backtest, "_proc_identity", lambda _pid: ("S", 99, 7))
    monkeypatch.setattr(
        backtest,
        "_terminate_backtest_worker",
        lambda worker, timeout=3.0: terminated.append(worker.process) or True,
    )

    with pytest.raises(RuntimeError, match="partial start"):
        backtest._execute_backtest_process("partial", set(), object(), ())

    assert terminated == [process]
    assert context.queue.closed is True
    assert context.queue.cancelled is True


def test_process_group_handshake_failure_uses_verified_cleanup(monkeypatch):
    process = _Process()
    context = _Context(process)
    terminated = []
    monkeypatch.setattr(backtest.mp, "get_context", lambda _name: context)
    monkeypatch.setattr(backtest, "_proc_identity", lambda _pid: ("S", 99, 7))

    def terminate(worker, timeout=3.0):
        del timeout
        terminated.append(worker)
        process.alive = False
        return True

    monkeypatch.setattr(backtest, "_terminate_backtest_worker", terminate)

    with pytest.raises(RuntimeError, match="isolated process group"):
        backtest._execute_backtest_process("handshake", set(), object(), ())

    assert len(terminated) == 1
    assert terminated[0].process is process


def test_normal_result_fails_closed_and_retains_unproven_cleanup(monkeypatch):
    run_id = "cleanup"
    process = _Process()
    context = _Context(process)
    monkeypatch.setattr(backtest.mp, "get_context", lambda _name: context)
    monkeypatch.setattr(backtest, "_proc_identity", lambda _pid: ("S", process.pid, 7))
    monkeypatch.setattr(backtest, "_terminate_backtest_worker", lambda _worker: False)

    try:
        with pytest.raises(RuntimeError, match="process tree did not stop"):
            backtest._execute_backtest_process(run_id, set(), object(), ())

        retained = backtest._active_backtest_worker(run_id)
        assert retained is not None
        assert retained.process is process
        assert context.queue.closed is True
    finally:
        retained = backtest._active_backtest_worker(run_id)
        if retained is not None:
            backtest._unregister_backtest_worker(run_id, retained)


def test_retained_worker_retry_unregisters_only_after_cleanup_proven(monkeypatch):
    run_id = "cleanup-retry"
    worker = backtest._BacktestWorker(
        process=_Process(),
        out=_Queue(),
        process_group=4242,
        start_time=7,
    )
    cleanup_results = iter((False, True))
    monkeypatch.setattr(
        backtest,
        "_terminate_backtest_worker",
        lambda _worker, timeout=3.0: next(cleanup_results),
    )
    backtest._register_backtest_worker(run_id, worker)

    try:
        assert backtest._cleanup_registered_backtest_worker(run_id, worker) is False
        assert backtest._active_backtest_worker(run_id) is worker
        assert backtest._cleanup_registered_backtest_worker(run_id, worker) is True
        assert backtest._active_backtest_worker(run_id) is None
    finally:
        backtest._unregister_backtest_worker(run_id, worker)


def test_terminal_run_cancel_retries_retained_worker_cleanup(monkeypatch):
    run_id = "terminal-cleanup-retry"
    worker = backtest._BacktestWorker(
        process=_Process(),
        out=_Queue(),
        process_group=4242,
        start_time=7,
    )
    state = SimpleNamespace(
        backtest_store=SimpleNamespace(
            get_run=lambda requested: (
                SimpleNamespace(status="failed") if requested == run_id else None
            )
        ),
        backtest_cancel_requests=set(),
    )
    monkeypatch.setattr(
        backtest,
        "_terminate_backtest_worker",
        lambda _worker, timeout=3.0: True,
    )
    backtest._register_backtest_worker(run_id, worker)

    try:
        response = asyncio.run(backtest.run_action(run_id, "cancel", state))
        assert response["accepted"] is False
        assert response["status"] == "failed"
        assert worker.cancel_requested.is_set()
        assert backtest._active_backtest_worker(run_id) is None
    finally:
        backtest._unregister_backtest_worker(run_id, worker)


def test_registration_failure_fails_closed_when_cleanup_is_unproven(monkeypatch):
    process = _Process()
    context = _Context(process)
    monkeypatch.setattr(backtest.mp, "get_context", lambda _name: context)
    monkeypatch.setattr(backtest, "_proc_identity", lambda _pid: ("S", process.pid, 7))
    monkeypatch.setattr(
        backtest,
        "_register_backtest_worker",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("registration failed")),
    )
    monkeypatch.setattr(backtest, "_terminate_backtest_worker", lambda _worker: False)

    with pytest.raises(RuntimeError, match="registration cleanup was not proven"):
        backtest._execute_backtest_process("registration", set(), object(), ())

    assert context.queue.closed is True
    assert context.queue.cancelled is True


def test_unverified_process_group_is_never_signalled(monkeypatch):
    process = _Process()
    process.pid = 100
    process.alive = True
    queue = _Queue(process)
    cleanup_complete = threading.Event()
    worker = backtest._BacktestWorker(
        process=process,
        out=queue,
        process_group=None,
        start_time=11,
        cleanup_complete=cleanup_complete,
    )
    groups = []

    def identity(pid):
        if not process.alive:
            return None
        return ("S", 999, 11) if pid == 100 else None

    monkeypatch.setattr(backtest, "_proc_identity", identity)
    monkeypatch.setattr(backtest, "_pidfd_open", lambda _pid: 77)
    monkeypatch.setattr(backtest.os, "close", lambda _descriptor: None)

    def send_signal(_descriptor, sig):
        assert sig == signal.SIGTERM
        process.alive = False
        cleanup_complete.set()

    monkeypatch.setattr(backtest, "_pidfd_send_signal", send_signal)
    monkeypatch.setattr(backtest.os, "kill", lambda *_args: None)
    monkeypatch.setattr(backtest.os, "killpg", lambda pgid, _sig: groups.append(pgid))

    assert backtest._terminate_backtest_worker(worker, timeout=0.1) is True
    assert groups == []


def test_verified_process_group_is_never_signalled_by_numeric_pgid(monkeypatch):
    process = _Process(pid=100)
    process.alive = True
    queue = _Queue(process)
    cleanup_complete = threading.Event()
    worker = backtest._BacktestWorker(
        process=process,
        out=queue,
        process_group=100,
        start_time=11,
        cleanup_complete=cleanup_complete,
    )
    groups = []

    monkeypatch.setattr(
        backtest,
        "_proc_identity",
        lambda pid: ("S", 100, 11) if pid == 100 and process.alive else None,
    )
    monkeypatch.setattr(backtest, "_pidfd_open", lambda _pid: 77)
    monkeypatch.setattr(backtest.os, "close", lambda _descriptor: None)
    monkeypatch.setattr(backtest.os, "kill", lambda *_args: None)
    monkeypatch.setattr(backtest.os, "killpg", lambda pgid, sig: groups.append((pgid, sig)))

    def send_signal(_descriptor, sig):
        assert sig == signal.SIGTERM
        process.alive = False
        cleanup_complete.set()

    monkeypatch.setattr(backtest, "_pidfd_send_signal", send_signal)

    assert backtest._terminate_backtest_worker(worker, timeout=0.1) is True
    assert groups == []


def test_post_start_identity_error_still_cleans_process_marker_and_queue(monkeypatch):
    process = _Process()
    context = _Context(process)
    terminated = []
    monkeypatch.setattr(backtest.mp, "get_context", lambda _name: context)
    monkeypatch.setattr(
        backtest,
        "_proc_identity",
        lambda _pid: (_ for _ in ()).throw(RuntimeError("identity lookup failed")),
    )

    def terminate(worker, timeout=3.0):
        del timeout
        terminated.append(worker.process)
        process.alive = False
        return True

    monkeypatch.setattr(backtest, "_terminate_backtest_worker", terminate)

    with pytest.raises(RuntimeError, match="identity lookup failed"):
        backtest._execute_backtest_process("post-start-error", set(), object(), ())

    assert terminated == [process]
    assert backtest._backtest_worker_is_starting("post-start-error") is False
    assert context.queue.closed is True
    assert context.queue.cancelled is True


def test_successful_supervisor_proof_is_recorded_as_idempotent(monkeypatch):
    process = _Process(pid=100)
    process.alive = False
    cleanup_complete = threading.Event()
    cleanup_complete.set()
    worker = backtest._BacktestWorker(
        process=process,
        out=object(),
        process_group=None,
        start_time=1,
        cleanup_complete=cleanup_complete,
    )
    monkeypatch.setattr(backtest, "_proc_identity", lambda _pid: None)

    assert backtest._terminate_backtest_worker(worker, timeout=0.01) is True
    assert worker.cleanup_proven is True
    assert backtest._terminate_backtest_worker(worker, timeout=0.01) is True


def test_stopped_supervisor_without_cleanup_proof_fails_closed():
    process = _Process(pid=100)
    process.alive = False
    worker = backtest._BacktestWorker(
        process=process,
        out=object(),
        process_group=100,
        start_time=1,
        cleanup_complete=threading.Event(),
    )

    assert backtest._terminate_backtest_worker(worker, timeout=0.01) is False
    assert worker.cleanup_proven is False


def test_cleanup_proof_read_error_fails_closed():
    class BrokenProof:
        def is_set(self):
            raise RuntimeError("proof unavailable")

    process = _Process(pid=100)
    process.alive = False
    worker = backtest._BacktestWorker(
        process=process,
        out=object(),
        process_group=None,
        start_time=1,
        cleanup_complete=BrokenProof(),
    )

    assert backtest._terminate_backtest_worker(worker, timeout=0.01) is False
    assert worker.cleanup_proven is False


def test_registration_cleanup_exception_still_clears_starting_and_queue(monkeypatch):
    process = _Process()
    context = _Context(process)
    monkeypatch.setattr(backtest.mp, "get_context", lambda _name: context)
    monkeypatch.setattr(backtest, "_proc_identity", lambda _pid: ("S", process.pid, 7))
    monkeypatch.setattr(
        backtest,
        "_register_backtest_worker",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("registration failed")),
    )
    monkeypatch.setattr(
        backtest,
        "_terminate_backtest_worker",
        lambda _worker: (_ for _ in ()).throw(RuntimeError("cleanup exploded")),
    )

    with pytest.raises(RuntimeError, match="registration cleanup was not proven"):
        backtest._execute_backtest_process("registration-raises", set(), object(), ())

    assert backtest._backtest_worker_is_starting("registration-raises") is False
    assert context.queue.closed is True
    assert context.queue.cancelled is True


def test_exited_root_never_reuses_numeric_process_group(monkeypatch):
    process = _Process(pid=100)
    process.alive = False
    worker = backtest._BacktestWorker(
        process=process,
        out=object(),
        process_group=100,
        start_time=1,
        lock=threading.Lock(),
    )
    groups = []
    monkeypatch.setattr(backtest, "_proc_identity", lambda _pid: None)
    monkeypatch.setattr(backtest.os, "killpg", lambda pgid, _sig: groups.append(pgid))

    assert backtest._terminate_backtest_worker(worker, timeout=0.01) is False
    assert groups == []


def test_isolation_cleanup_exception_still_clears_starting_and_queue(monkeypatch):
    process = _Process()
    context = _Context(process)
    monkeypatch.setattr(backtest.mp, "get_context", lambda _name: context)
    monkeypatch.setattr(backtest, "_proc_identity", lambda _pid: ("S", process.pid + 1, 7))
    monkeypatch.setattr(
        backtest,
        "_terminate_backtest_worker",
        lambda _worker: (_ for _ in ()).throw(RuntimeError("cleanup exploded")),
    )

    with pytest.raises(RuntimeError, match="isolation failed and its cleanup was not proven"):
        backtest._execute_backtest_process("isolation-raises", set(), object(), ())

    assert backtest._backtest_worker_is_starting("isolation-raises") is False
    assert context.queue.closed is True
    assert context.queue.cancelled is True


def test_real_supervisor_success_path():
    assert backtest._execute_backtest_process(
        "supervisor-success",
        set(),
        _successful_entry,
        ("result",),
    ) == "result"


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Linux fork containment regression")
def test_dedicated_supervisor_contains_abrupt_worker_descendant(tmp_path):
    pid_path = tmp_path / "detached.pid"
    marker_path = tmp_path / "escaped.marker"
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    cleanup_complete = ctx.Event()
    supervisor = None
    detached_pid = None
    detached_start = None
    try:
        supervisor = ctx.Process(
            target=backtest._supervised_backtest_process_entry,
            args=(
                out,
                _abrupt_entry_with_detached_child,
                (str(pid_path), str(marker_path)),
                cleanup_complete,
            ),
        )
        supervisor.start()
        status, exc_name, message, _tb = out.get(timeout=10)
        assert status == "err"
        assert exc_name == "RuntimeError"
        assert "abrupt" in message.lower()

        deadline = time.monotonic() + 2.0
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_path.is_file()
        detached_pid = int(pid_path.read_text(encoding="utf-8"))
        detached_identity = backtest._proc_identity(detached_pid)
        detached_start = detached_identity[2] if detached_identity is not None else None

        root_identity = backtest._proc_identity(supervisor.pid)
        assert root_identity is not None
        worker = backtest._BacktestWorker(
            process=supervisor,
            out=out,
            process_group=supervisor.pid,
            start_time=root_identity[2],
            cleanup_complete=cleanup_complete,
        )
        assert backtest._terminate_backtest_worker(worker, timeout=2.0) is True
        supervisor.join(timeout=1.0)
        time.sleep(0.7)
        assert not marker_path.exists()
        assert backtest._proc_identity(detached_pid) is None
    finally:
        if supervisor is not None and supervisor.is_alive():
            supervisor.kill()
            supervisor.join(timeout=1.0)
        if detached_pid is not None and detached_start is not None:
            identity = backtest._proc_identity(detached_pid)
            if identity is not None and identity[2] == detached_start:
                try:
                    os.kill(detached_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        out.close()
        out.cancel_join_thread()


@pytest.mark.skipif(not hasattr(os, "fork"), reason="Linux fork containment regression")
def test_supervisor_cleans_abrupt_descendant_before_reporting_error(tmp_path):
    pid_path = tmp_path / "detached.pid"
    marker_path = tmp_path / "escaped.marker"
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    cleanup_complete = ctx.Event()
    supervisor = ctx.Process(
        target=backtest._supervised_backtest_process_entry,
        args=(
            out,
            _abrupt_entry_with_detached_child,
            (str(pid_path), str(marker_path)),
            cleanup_complete,
        ),
    )
    detached_pid = None
    detached_start = None
    try:
        supervisor.start()
        status, exc_name, message, _tb = out.get(timeout=10)
        assert status == "err"
        assert exc_name == "RuntimeError"
        assert "abrupt" in message.lower()
        supervisor.join(timeout=2)
        assert supervisor.is_alive() is False

        deadline = time.monotonic() + 1.0
        while not pid_path.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert pid_path.is_file()
        detached_pid = int(pid_path.read_text(encoding="utf-8"))
        identity = backtest._proc_identity(detached_pid)
        detached_start = identity[2] if identity is not None else None
        time.sleep(0.7)
        assert not marker_path.exists()
        assert backtest._proc_identity(detached_pid) is None
    finally:
        if supervisor.is_alive():
            supervisor.kill()
            supervisor.join(timeout=1)
        if detached_pid is not None and detached_start is not None:
            identity = backtest._proc_identity(detached_pid)
            if identity is not None and identity[2] == detached_start:
                try:
                    os.kill(detached_pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        out.close()
        out.cancel_join_thread()
