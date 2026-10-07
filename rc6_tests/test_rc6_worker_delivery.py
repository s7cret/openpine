"""Actual compiled owners and fenced durable ACK through additive adapters."""

from dataclasses import replace
from io import BytesIO
import json
import os
import subprocess
import sys
import time
from typing import cast, BinaryIO

import pytest
from openpine_contracts import seal_content_hash
from openpine_contracts.worker_delivery import decode_frame, encode_frame

from openpine.jobs.execution_store import JobExecutionStore
from openpine.jobs.transactional_store import JobV1Error
from openpine.runtime.job_checkpoint import decode_job_checkpoint
from openpine.runtime.isolated_worker import _DeliveryPipe, _close_process_pipes
from openpine.runtime.rc6_config import serialize_engine_config
from openpine.runtime.worker_capabilities import WORKER_CAPABILITIES
from openpine.runtime.worker_delivery import (
    DELIVERY_CAPABILITIES,
    InteractiveDeliveryReceiver,
    JobDeliverySender,
    read_batch,
    write_batch,
)
from rc6_tests.test_rc6_durable_job_cut import descriptor, fresh, inputs, resume
from rc6_tests.test_rc6_worker_restore_admission import receiver


@pytest.fixture(scope="module")
def compiled():
    return descriptor()


@pytest.fixture
def channel(compiled, tmp_path):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        execution, _ = fresh(store, compiled)
        sender = execution.delivery_sender(requested_capabilities=DELIVERY_CAPABILITIES)
        driver = receiver(compiled)
        worker = InteractiveDeliveryReceiver(
            driver, sender.binding, sender.wire, requested_capabilities=DELIVERY_CAPABILITIES
        )
        sender.negotiate(worker.hello(), now_ms=1000)
        yield store, sender, worker


def exchange(worker, frames):
    pipe = BytesIO()
    write_batch(pipe, frames)
    pipe.seek(0)
    return worker.receive_batch(read_batch(pipe))


def test_real_restore_suffix_and_durable_ack_survive_job_reopen(channel, compiled):
    store, sender, worker = channel
    original = worker.driver.session.export_state()
    frames = sender.restore_batch(now_ms=1000)
    assert exchange(worker, frames) == 0
    assert worker.driver.session.export_state() != original
    for ack in worker.committed_acks(sender.wire):
        assert sender.accept_ack(ack, now_ms=1000)
        assert not sender.accept_ack(ack, now_ms=1000)
    before = worker.driver.session.export_state()
    assert exchange(worker, frames) == 0
    assert worker.driver.session.export_state() == before
    actual, engine, result = resume(store, compiled, worker_id="worker-1", generation=1)
    suffix = sender.committed_batch(now_ms=1000)
    assert suffix and exchange(worker, suffix) == len(suffix)
    assert engine.equity == 1060 and result.resume_state.bar_index == 2
    assert worker.driver.session.export_state() == actual.session.export_state()
    before = worker.driver.session.export_state()
    assert exchange(worker, suffix) == 0 and worker.driver.session.export_state() == before
    wire = store.checkpoint("job", "durable-run")
    acks = worker.committed_acks(wire)
    assert acks
    for ack in acks:
        assert sender.accept_ack(ack, now_ms=1000)
    with JobExecutionStore(store.path) as reopened:
        p = decode_job_checkpoint(reopened.checkpoint("job", "durable-run"))
        assert p["last_acknowledged_frame"] == p["committed_sequence"]
        assert not reopened.pending_frames("job", "durable-run", 1, now_ms=1000)


@pytest.mark.parametrize("change", ["generation", "worker", "job", "schema", "capabilities"])
def test_peer_negotiation_refuses_before_activation(channel, change):
    _, sender, worker = channel
    value = decode_frame(worker.hello())
    if change == "generation":
        value["worker_generation"] += 1
    elif change == "worker":
        value["worker_id"] = "foreign-worker"
    elif change == "job":
        value["job_id"] = "foreign-job"
    elif change == "schema":
        value["body"]["delivery_schema_hash"] = "sha256:" + "a" * 64
    else:
        value["body"]["capabilities"] = ["closed_bar"]
    wire = json.dumps(seal_content_hash(value, schema_id=value["schema_id"])).encode()
    before = worker.driver.session.export_state()
    with pytest.raises(ValueError):
        sender.negotiate(wire, now_ms=1000)
    assert worker.driver.session.export_state() == before


@pytest.mark.parametrize(
    "caps",
    [
        (),
        ("closed_bar",),
        ("delivery_v1",),
        ("committed_job_v1",),
        (*DELIVERY_CAPABILITIES, "unknown"),
    ],
)
def test_explicit_both_sides_opt_in_required(channel, caps):
    store, sender, worker = channel
    with pytest.raises(ValueError, match="explicit"):
        JobDeliverySender(
            store, sender.binding, sender.context, requested_capabilities=caps, now_ms=1000
        )
    with pytest.raises(ValueError, match="explicit"):
        InteractiveDeliveryReceiver(
            worker.driver, sender.binding, sender.wire, requested_capabilities=caps
        )
    assert WORKER_CAPABILITIES == ("closed_bar",)


@pytest.mark.parametrize("change", ["generation", "worker", "job", "lease"])
def test_active_owner_fence_refuses_before_delivery(channel, change):
    store, sender, _ = channel
    binding = sender.binding
    if change == "lease":
        now = 10001
    else:
        now = 1000
        binding = replace(
            binding,
            **{"worker_generation": 2}
            if change == "generation"
            else {"worker_id": "foreign"}
            if change == "worker"
            else {"job_id": "foreign"},
        )
    before = store.checkpoint("job", "durable-run")
    with pytest.raises(JobV1Error):
        JobDeliverySender(
            store, binding, sender.context, requested_capabilities=DELIVERY_CAPABILITIES, now_ms=now
        )
    assert store.checkpoint("job", "durable-run") == before


@pytest.mark.parametrize("change", ["stale", "gap", "message", "checkpoint", "role"])
def test_ack_refusal_preserves_durable_cursor(channel, change):
    store, sender, worker = channel
    exchange(worker, sender.restore_batch(now_ms=1000))
    ack = worker.committed_acks(sender.wire)[0]
    value = decode_frame(ack)
    if change == "stale":
        value["worker_generation"] += 1
    elif change == "gap":
        value["body"]["sequence"] += 1
    elif change == "message":
        value["body"]["message_hash"] = "sha256:" + "b" * 64
    elif change == "checkpoint":
        value["body"]["checkpoint_hash"] = "sha256:" + "b" * 64
    else:
        value["sender_role"] = "worker" if value["sender_role"] == "parent" else "parent"
    bad = encode_frame(value)
    before = store.checkpoint("job", "durable-run")
    with pytest.raises(ValueError):
        sender.accept_ack(bad, now_ms=1000)
    assert store.checkpoint("job", "durable-run") == before


@pytest.mark.parametrize("damage", ["eof", "huge", "truncated", "empty"])
def test_bounded_transport_refuses_without_owner_mutation(channel, damage):
    _, sender, worker = channel
    pipe = BytesIO()
    write_batch(pipe, sender.restore_batch(now_ms=1000))
    raw = pipe.getvalue()
    raw = (
        raw[:-4]
        if damage == "eof"
        else raw[:-10]
        if damage == "truncated"
        else ((262145).to_bytes(4, "big") if damage == "huge" else b"\0\0\0\0")
    )
    before = worker.driver.session.export_state()
    with pytest.raises(ValueError):
        read_batch(BytesIO(raw))
    assert worker.driver.session.export_state() == before


def test_real_process_negotiates_restores_executes_suffix_and_emits_committed_ack(
    channel, compiled, tmp_path
):
    store, sender, _ = channel
    resume(store, compiled, worker_id="worker-1", generation=1)
    packet = sender.prepare_exchange(now_ms=1000)
    config, _, _ = inputs(compiled)
    request = {
        "source": compiled["artifact"]["python_code"],
        "generated_artifact": compiled["artifact"]["generated_artifact"],
        "execution_context": compiled["context"],
        "engine_config": serialize_engine_config(config, "strict_5x"),
        "params": {},
        "worker_delivery": sender.descriptor(),
    }
    entrypoint = (
        "import sys,json; from openpine.runtime.worker_delivery import run_delivery_exchange; "
        "assert sys.version_info[:3]==(3,13,5) and sys._is_gil_enabled(); "
        "request=json.loads(sys.stdin.buffer.readline()); "
        "sys.exit(run_delivery_exchange(request,sys.stdin.buffer,sys.stdout.buffer))"
    )
    with (tmp_path / "worker.stderr").open("wb") as stderr:
        proc = subprocess.Popen(  # noqa: S603 - fixed test interpreter and literal entrypoint
            [sys.executable, "-B", "-c", entrypoint],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=stderr,
            bufsize=0,
            env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
        )
        try:
            assert proc.stdin is not None and proc.stdout is not None
            deadline = time.monotonic() + 30
            outgoing = _DeliveryPipe(proc.stdin.fileno(), deadline)
            incoming = _DeliveryPipe(proc.stdout.fileno(), deadline)
            raw = json.dumps(request).encode() + b"\n"
            while raw:
                raw = raw[outgoing.write(raw) :]
            hello = read_batch(cast(BinaryIO, incoming))
            assert len(hello) == 1 and proc.pid != os.getpid()
            sender.negotiate(hello[0], now_ms=1000)
            sender.history = list(packet)
            write_batch(cast(BinaryIO, outgoing), packet)
            acks = read_batch(cast(BinaryIO, incoming))
            proc.stdin.close()
            assert proc.wait(timeout=10) == 0
            assert acks and all(sender.accept_ack(w, now_ms=1000) for w in acks)
            cut = decode_job_checkpoint(store.checkpoint("job", "durable-run"))
            assert cut["last_acknowledged_frame"] == cut["committed_sequence"]
            assert incoming.bytes > 0 and outgoing.bytes > 0
        finally:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=5)
            _close_process_pipes(proc)


def test_parent_launcher_path_uses_real_pipe_exchange_with_mocked_protected_facilities(
    channel, compiled, monkeypatch
):
    from openpine.runtime import isolated_worker as launcher

    store, sender, _ = channel
    resume(store, compiled, worker_id="worker-1", generation=1)
    config, _, _ = inputs(compiled)
    request = {
        "source": compiled["artifact"]["python_code"],
        "generated_artifact": compiled["artifact"]["generated_artifact"],
        "execution_context": compiled["context"],
        "engine_config": serialize_engine_config(config, "strict_5x"),
        "params": {},
    }
    entry = (
        "import sys,json; from openpine.runtime.worker_delivery import run_delivery_exchange; "
        "request=json.loads(sys.stdin.buffer.readline()); "
        "sys.exit(run_delivery_exchange(request,sys.stdin.buffer,sys.stdout.buffer))"
    )
    units = []
    monkeypatch.setattr(
        launcher, "_bwrap_argv", lambda manifest, unit: [sys.executable, "-B", "-c", entry]
    )

    def cleanup(proc, unit):
        if proc.poll() is None:
            proc.kill()
        proc.wait(timeout=5)
        _close_process_pipes(proc)
        units.append(unit)

    monkeypatch.setattr(launcher, "_cleanup_worker_process", cleanup)
    result = launcher.execute_committed_delivery(
        sender, request, admitted_manifest={}, clock=lambda: 1000
    )
    assert result["returncode"] == 0 and result["acknowledged_frames"] > 0
    assert result["worker_generation"] == 1 and result["worker_pid"] != os.getpid()
    assert units == [result["worker_unit"]]
    cut = decode_job_checkpoint(store.checkpoint("job", "durable-run"))
    assert cut["last_acknowledged_frame"] == cut["committed_sequence"]
