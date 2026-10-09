"""Abstract multiprocessing failure contracts retain ownership until verified cleanup."""

from types import SimpleNamespace
import uuid
import pytest
from openpine.gateway.routes import backtest as b


@pytest.mark.parametrize(
    "stage",
    [
        "start-EOF",
        "start-invalid",
        "start-mismatch",
        "setup-EOF",
        "setup-invalid",
        "setup-mismatch",
        "identity-drift",
        "isolation",
        "registration",
        "compute-cancel",
    ],
)
@pytest.mark.parametrize("cleanup", ["verified", "unverified", "raises"])
def test_startup_failure_cannot_drop_unverified_worker_ownership(monkeypatch, stage, cleanup):
    run = "startup-contract-" + uuid.uuid4().hex
    events = []
    cancel = set()
    alive = [True]

    class Output:
        def close(self):
            events.append("queue-close")

        def cancel_join_thread(self):
            events.append("queue-cancel-join")

    class Receiver:
        def recv(self):
            if stage.endswith("EOF"):
                raise EOFError()
            if stage.endswith("invalid"):
                return (True, 3)
            if stage.endswith("mismatch"):
                return (778, 3)
            return (777, 3)

        def close(self):
            events.append("receiver-close")

    sender = SimpleNamespace(close=lambda: events.append("sender-close"))

    class Proc:
        pid = 777

        def start(self):
            if stage.startswith("start-"):
                raise OSError("partial start refused")

        def is_alive(self):
            return alive[0]

    ctx = SimpleNamespace(
        Queue=lambda: Output(),
        Event=lambda: SimpleNamespace(is_set=lambda: False),
        Pipe=lambda **kw: (Receiver(), sender),
        Process=lambda **kw: Proc(),
    )
    monkeypatch.setattr(b.mp, "get_context", lambda name: ctx)
    monkeypatch.setattr(
        b, "_proc_identity", lambda pid: None if stage == "identity-drift" else ("S", pid, 3)
    )
    if stage == "isolation":
        monkeypatch.setattr(b, "_BACKTEST_ISOLATION_TIMEOUT_SECONDS", 0)
    original = b._register_backtest_worker

    def register(run, worker):
        if stage == "registration":
            raise RuntimeError("registration refused")
        original(run, worker)
        if stage == "compute-cancel":
            cancel.add(run)

    monkeypatch.setattr(b, "_register_backtest_worker", register)

    def terminate(worker, timeout=3.0):
        events.append("cleanup")
        if cleanup == "raises":
            raise RuntimeError("cleanup observation failed")
        if cleanup == "verified":
            alive[0] = False
        return cleanup == "verified"

    monkeypatch.setattr(b, "_terminate_backtest_worker", terminate)
    try:
        with pytest.raises((OSError, RuntimeError, b._BacktestCancelled)):
            b._execute_backtest_process(run, cancel, lambda *a: None, ())
        workers = b._backtest_workers(run)
        # The startup EOF proves no launched child owned the capability, even
        # when no hypothetical cleanup callback was invoked.
        cleaned = (cleanup == "verified" and stage != "start-invalid") or stage == "start-EOF"
        assert bool(workers) == (not cleaned)
        assert not b._backtest_worker_is_starting(run)
        assert events.count("queue-close") == 1 and events.count("queue-cancel-join") == 1
        assert "receiver-close" in events and "sender-close" in events
        assert b._backtest_terminal_outcome(run) is None
    finally:
        for worker in b._backtest_workers(run):
            b._unregister_backtest_worker(run, worker)
        b._set_backtest_worker_starting(run, False)
