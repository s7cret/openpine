"""Transport and cleanup refusal contracts, not protected worker qualification."""

import io
import os
import subprocess
import time
from types import SimpleNamespace
import pytest
from openpine.runtime import isolated_worker as w
from tests.rc4_fixtures import admitted_manifest


@pytest.mark.parametrize(
    "out,err,code,error",
    [
        ("{", "", 0, "malformed"),
        ("{}", "", 0, "rejected"),
        ('{"ok":false,"error":"denied"}', "", 0, "denied"),
        ("", "untrusted error", 7, "untrusted error"),
        ("x" * 1000001, "", 0, "excessive"),
        ("", "x" * 1000001, 0, "excessive"),
        ('{"ok":true}', "", 0, None),
    ],
    ids=[
        "malformed",
        "empty-object",
        "denied",
        "nonzero",
        "large-stdout",
        "large-stderr",
        "success-transport",
    ],
)
def test_single_exchange_validates_output_before_return(monkeypatch, out, err, code, error):
    # Only pipe/result handling is exercised. No isolated process is represented.
    proc = SimpleNamespace(returncode=code, communicate=lambda **k: (out, err))
    monkeypatch.setattr(w, "_bwrap_argv", lambda *a, **k: ["test-transport"])
    monkeypatch.setattr(w.subprocess, "Popen", lambda *a, **k: proc)
    args = dict(
        source=b"class GeneratedStrategy: pass",
        stack_id="openpine-5.0",
        semantic_profile="strict_5x",
        admitted_manifest=admitted_manifest(),
    )
    if error:
        with pytest.raises(w.IsolatedWorkerError, match=error):
            w.evaluate_artifact(**args)
    else:
        assert w.evaluate_artifact(**args) == {"ok": True}


@pytest.mark.parametrize(
    "change,error",
    [
        ({"source": b"x" * 500001}, "size limit"),
        ({"stack_id": "foreign"}, "stack_id"),
        ({"semantic_profile": "unknown"}, "semantic_profile"),
    ],
)
def test_invalid_single_exchange_stops_before_spawn(monkeypatch, change, error):
    monkeypatch.setattr(
        w.subprocess, "Popen", lambda *a, **k: pytest.fail("invalid request spawned")
    )
    args = dict(
        source=b"x",
        stack_id="openpine-5.0",
        semantic_profile="strict_5x",
        admitted_manifest=admitted_manifest(),
    )
    args.update(change)
    with pytest.raises(w.IsolatedWorkerError, match=error):
        w.evaluate_artifact(**args)


@pytest.mark.parametrize(
    "failure", ["spawn", "cgroup-prepare", "cgroup-attach", "timeout", "cleanup-timeout"]
)
def test_single_exchange_errors_preserve_cleanup_failures(monkeypatch, failure):
    calls = []

    class Proc:
        def communicate(self, **kw):
            raise subprocess.TimeoutExpired("transport", 5)

    def spawn(*args, **kw):
        if failure == "spawn":
            raise OSError("spawn failed")
        return Proc()

    def prepare(*args):
        if failure == "cgroup-prepare":
            raise w.CgroupError("prepare refused")

    def attach(*args):
        if failure == "cgroup-attach":
            raise w.CgroupError("attach refused")

    def cleanup(*args):
        calls.append("cleanup")
        if failure == "cleanup-timeout":
            raise w.IsolatedWorkerError("cleanup remains unverified")

    monkeypatch.setattr(w, "_bwrap_argv", lambda *a, **k: ["test-transport"])
    monkeypatch.setattr(w.subprocess, "Popen", spawn)
    monkeypatch.setattr(w, "prepare_worker_cgroup", prepare)
    monkeypatch.setattr(w, "attach_worker_tree", attach)
    monkeypatch.setattr(w, "_cleanup_worker_process", cleanup)
    # attach accesses only a fixture PID; it is never signalled.
    Proc.pid = 123
    with pytest.raises(w.IsolatedWorkerError):
        w.evaluate_artifact(
            b"x",
            admitted_manifest=admitted_manifest(),
            cgroup_dir="contract-only",
            stack_id="openpine-5.0",
            semantic_profile="strict_5x",
        )
    assert bool(calls) == (failure in {"cgroup-attach", "timeout", "cleanup-timeout"})


@pytest.mark.parametrize("failure", ["wait-oserror", "wait-timeout", "kill-oserror"])
def test_reap_failure_closes_every_pipe(monkeypatch, failure):
    pipes = [io.StringIO() for _ in range(3)]
    calls = []

    def wait(**kw):
        if failure == "wait-oserror":
            raise OSError("wait failed")
        raise subprocess.TimeoutExpired("owned", 2)

    def kill():
        calls.append("kill")
        if failure == "kill-oserror":
            raise OSError("kill failed")

    proc = SimpleNamespace(
        stdin=pipes[0], stdout=pipes[1], stderr=pipes[2], wait=wait, poll=lambda: None, kill=kill
    )
    with pytest.raises(w.IsolatedWorkerError, match="cleanup did not complete"):
        w._reap_worker_process_bounded(proc)
    assert all(p.closed for p in pipes)


@pytest.mark.parametrize("unit_error,reap_error", [(True, True), (True, False), (False, True)])
def test_cleanup_error_precedence_keeps_retryable_unit_failure(monkeypatch, unit_error, reap_error):
    calls = []

    def stop(unit):
        calls.append("unit")
        if unit_error:
            raise w.IsolatedWorkerError("unit unverified")

    def reap(proc):
        calls.append("reap")
        if reap_error:
            raise w.IsolatedWorkerError("reap unverified")

    monkeypatch.setattr(w, "_stop_worker_unit", stop)
    monkeypatch.setattr(w, "_reap_worker_process_bounded", reap)
    proc = SimpleNamespace(
        poll=lambda: None, kill=lambda: (_ for _ in ()).throw(OSError("kill failed"))
    )
    with pytest.raises(w.IsolatedWorkerError, match="unit" if unit_error else "reap") as caught:
        w._cleanup_worker_process(proc, "contract-only")
    assert calls == ["unit", "reap"] and caught.value.__cause__ is not None


@pytest.mark.parametrize(
    "payload", ["{", "[]", "null", "\xff", '{"error_code":"DENIED","error":"not admitted"}']
)
def test_interactive_stream_rejects_malformed_or_bootstrap_error(payload):
    session = object.__new__(w.InteractiveWorkerSession)
    session.proc = SimpleNamespace(stdout=io.BytesIO(), stderr=None)
    session.timeout_s = 0.1
    session._stdout_buffer = bytearray(payload.encode("latin1") + b"\n")
    session.bytes_received = 0
    with pytest.raises(w.IsolatedWorkerError):
        session._read_message()


@pytest.mark.parametrize(
    "payload",
    [None, "x\ny", "x\ry", "x" * (w.WORKER_LINE_LIMIT_BYTES + 1)],
    ids=["nontext", "newline", "carriage-return", "oversize"],
)
def test_writer_refuses_multiline_and_oversized_input_without_touching_pipe(payload):
    session = object.__new__(w.InteractiveWorkerSession)
    session._closed = False
    sink = io.StringIO()
    session.proc = SimpleNamespace(stdin=sink)
    session.bytes_sent = 0
    with pytest.raises(w.IsolatedWorkerError):
        session._write_serialized_json_line(payload)
    assert sink.getvalue() == "" and session.bytes_sent == 0


@pytest.mark.parametrize("kind", ["before-deadline", "expired"])
def test_delivery_pipe_deadline_refuses_empty_read_and_blocked_write(kind):
    readfd, writefd = os.pipe()
    try:
        deadline = time.monotonic() + (0.001 if kind == "before-deadline" else -1)
        pipe = w._DeliveryPipe(readfd, deadline)
        with pytest.raises(w.IsolatedWorkerError, match="timed out"):
            pipe.read(1)
        writer = w._DeliveryPipe(writefd, time.monotonic() - 1)
        with pytest.raises(w.IsolatedWorkerError, match="timed out"):
            writer.write(b"x")
        assert pipe.bytes == 0 and writer.bytes == 0
    finally:
        os.close(readfd)
        os.close(writefd)


@pytest.mark.parametrize(
    "response",
    [
        {"error_type": "Admission", "detail": "refused"},
        {"error_code": "DENIED", "error": "refused"},
        {},
    ],
)
def test_worker_error_response_is_never_returned_as_result(response):
    with pytest.raises(w.IsolatedWorkerError):
        w.InteractiveWorkerSession._raise_response(response)


@pytest.mark.parametrize("mutation", ["kind", "body", "run_id", "bar_index", "recalc_iteration"])
def test_callback_rejects_foreign_bar_identity(mutation):
    event = {
        "run_id": "run-a",
        "bar_index": 3,
        "bar_open_time_utc_ms": 123,
        "recalc_iteration": 0,
        "bar_hash": "h",
        "bar": {},
        "broker_projection": {},
        "execution_event": {},
    }
    body = {k: event[k] for k in ("run_id", "bar_index", "recalc_iteration")}
    response = {"kind": "INTENT_BATCH", "body": body}
    if mutation == "kind":
        response["kind"] = "HELLO"
    elif mutation == "body":
        response["body"] = []
    else:
        body[mutation] = "foreign"
    session = object.__new__(w.InteractiveWorkerSession)
    session.protocol = SimpleNamespace(append=lambda *a, **k: {})
    session._request = lambda value: response
    with pytest.raises(w.IsolatedWorkerError):
        session.evaluate_bar(event)


@pytest.mark.parametrize(
    "mutation",
    [
        "result-kind",
        "intent-kind",
        "body",
        "run_id",
        "bar_index",
        "recalc_iteration",
        "message_id",
        "hash",
    ],
)
def test_recalculation_rejects_cross_callback_results(mutation):
    event = {
        "run_id": "run-a",
        "bar_index": 3,
        "bar_open_time_utc_ms": 123,
        "recalc_iteration": 1,
        "broker_event_batch_hash": "h",
        "broker_events": [],
        "execution_event": {},
        "broker_projection_hash": "p",
        "broker_projection": {},
    }
    body = {k: event[k] for k in ("run_id", "bar_index", "recalc_iteration")}
    body["intent_batch_hash"] = "batch"
    result = {"kind": "RECALC_RESULT", "body": {**body, "intent_batch_message_id": "msg"}}
    intent = {"kind": "INTENT_BATCH", "body": dict(body), "message_id": "msg"}
    if mutation == "result-kind":
        result["kind"] = "HELLO"
    elif mutation == "intent-kind":
        intent["kind"] = "HELLO"
    elif mutation == "body":
        intent["body"] = []
    elif mutation == "message_id":
        intent["message_id"] = "foreign"
    elif mutation == "hash":
        intent["body"]["intent_batch_hash"] = "foreign"
    else:
        result["body"][mutation] = "foreign"
    session = object.__new__(w.InteractiveWorkerSession)
    session.protocol = SimpleNamespace(append=lambda *a, **k: {"sequence": 7})
    session._write_message = lambda value: None
    responses = iter([result, intent])
    session._read_message = lambda: next(responses)
    with pytest.raises(w.IsolatedWorkerError):
        session.evaluate_recalc(event)
