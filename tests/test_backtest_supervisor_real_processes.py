"""Real process ownership at the gateway compute boundary (no fake PIDs)."""
from __future__ import annotations

import multiprocessing as mp
import os
import signal
import subprocess
import sys
import time
import uuid

import pytest

from openpine.gateway.routes import backtest


def _return_value(out, value: str) -> None:
    out.put(("ok", value))


def _raise_from_callable(_out) -> None:
    raise ValueError("owned callable failed")


def _exit_without_ack(_out) -> None:
    os._exit(17)


def test_owned_process_returns_exact_result_and_releases_registration() -> None:
    run_id = uuid.uuid4().hex
    assert backtest._execute_backtest_process(
        run_id, set(), _return_value, ("canonical-result",), context_name="spawn"
    ) == "canonical-result"
    assert run_id not in backtest._ACTIVE_BACKTEST_WORKERS
    assert run_id not in backtest._RETAINED_BACKTEST_WORKERS
    assert run_id not in backtest._STARTING_BACKTEST_RUNS


def test_owned_process_forwards_callable_failure_without_leaking_registration() -> None:
    run_id = uuid.uuid4().hex
    with pytest.raises(RuntimeError, match="ValueError: owned callable failed"):
        backtest._execute_backtest_process(
            run_id, set(), _raise_from_callable, (), context_name="spawn"
        )
    assert run_id not in backtest._ACTIVE_BACKTEST_WORKERS
    assert run_id not in backtest._RETAINED_BACKTEST_WORKERS


def test_owned_process_reports_abrupt_child_exit_without_fabricating_success() -> None:
    run_id = uuid.uuid4().hex
    with pytest.raises(RuntimeError, match="backtest callable exited abruptly with code 17"):
        backtest._execute_backtest_process(
            run_id, set(), _exit_without_ack, (), context_name="spawn"
        )
    assert run_id not in backtest._ACTIVE_BACKTEST_WORKERS
    assert run_id not in backtest._RETAINED_BACKTEST_WORKERS


def test_startup_identity_roundtrip_uses_real_pipe_and_rejects_bad_values() -> None:
    identity = backtest._proc_identity(os.getpid())
    assert identity is not None
    recv, sender = mp.get_context("fork").Pipe(duplex=False)
    backtest._publish_backtest_startup_identity(sender)
    assert backtest._receive_backtest_startup_identity(recv) == (
        os.getpid(), identity[2]
    )
    recv, sender = mp.get_context("fork").Pipe(duplex=False)
    sender.send((0, -1))
    sender.close()
    with pytest.raises(RuntimeError, match="invalid backtest supervisor startup identity"):
        backtest._receive_backtest_startup_identity(recv)
    recv, sender = mp.get_context("fork").Pipe(duplex=False)
    sender.close()
    assert backtest._receive_backtest_startup_identity(recv) is None


def test_procfs_identity_and_pidfd_follow_one_owned_child() -> None:
    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(20)"],
        stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    descriptor = None
    try:
        identity = backtest._proc_identity(child.pid)
        assert identity is not None and identity[0] != "Z"
        assert identity[1] == os.getpgrp() and identity[2] > 0
        assert backtest._descendant_process_identities(os.getpid())[child.pid] == identity[2]
        descriptor = backtest._pidfd_open(child.pid)
        assert backtest._pidfd_has_exited(descriptor) is False
        assert backtest._process_tasks_are_stopped(child.pid, identity[2]) is False
        with pytest.raises(RuntimeError, match="process identity changed while stopping"):
            backtest._process_tasks_are_stopped(child.pid, identity[2] + 1)
        backtest._pidfd_send_signal(descriptor, signal.SIGSTOP)
        for _ in range(100):
            state = backtest._proc_identity(child.pid)
            if state is not None and state[0] in {"T", "t"}:
                break
            time.sleep(0.01)
        assert backtest._process_tasks_are_stopped(child.pid, identity[2]) is True
        backtest._pidfd_send_signal(descriptor, signal.SIGCONT)
        backtest._pidfd_send_signal(descriptor, signal.SIGTERM)
        assert child.wait(timeout=4) != 0
        assert backtest._pidfd_has_exited(descriptor) is True
        assert backtest._proc_identity(child.pid) is None
    finally:
        if child.poll() is None:
            child.kill()
            child.wait(timeout=4)
        if descriptor is not None:
            os.close(descriptor)


def test_procfs_missing_identity_is_not_a_stopped_process() -> None:
    absent_pid = 2**31 - 1
    assert backtest._proc_identity(absent_pid) is None
    assert backtest._process_tasks_are_stopped(absent_pid, 1) is False
