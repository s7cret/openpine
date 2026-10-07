"""Local worker admission/activation; this is not protected wire qualification."""

from dataclasses import replace

import pytest
from backtest_engine import JsonResumeStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from openpine_contracts import seal_content_hash, WorkerProtocolSemanticError

from openpine.jobs.execution_store import JobExecutionStore
from openpine.runtime.job_checkpoint import decode_job_checkpoint, encode_job_checkpoint
from openpine.runtime.rc6_worker_runtime import RC6InteractiveCallbacks
from openpine.runtime.worker_capabilities import (
    WORKER_CAPABILITIES,
    validate_requested_capabilities,
)
from openpine.runtime.worker_protocol import WorkerProtocolError
from rc6_tests.test_rc6_durable_job_cut import (
    descriptor,
    fresh,
    inputs,
    owner,
    resume,
)


@pytest.fixture(scope="module")
def compiled():
    return descriptor()


@pytest.fixture
def cut(compiled, tmp_path):
    with JobExecutionStore(tmp_path / "jobs.sqlite") as store:
        fresh(store, compiled)
        yield store, store.checkpoint("job", "durable-run")


def receiver(compiled):
    config, _, _ = inputs(compiled)
    return RC6InteractiveCallbacks(owner(compiled, config), compiled["context"])


def identities(driver):
    return (
        driver.session.session,
        driver.session.execution_cursor,
        driver.session._callback_receipts,
    )


def test_prepare_activation_and_causal_worker_suffix_equal_native_actual_control(compiled, cut):
    store, wire = cut
    driver = receiver(compiled)
    before, objects = driver.session.export_state(), identities(driver)
    staged = driver.prepare_job_restore(wire, job_id="job", worker_generation=1)
    assert driver.session.export_state() == before
    assert all(a is b for a, b in zip(objects, identities(driver), strict=True))
    assert staged.native_bytes == JsonResumeStateSerializer().dumps(
        JsonResumeStateSerializer().loads(staged.native_bytes)
    )
    assert driver.activate_job_restore(staged)
    committed = decode_job_checkpoint(wire)["protocol_messages"]
    assert driver.restored_protocol._sequence == len(committed)
    prefix_count = len(committed)
    actual, engine, result = resume(store, compiled)
    suffix = store.frames("job", "durable-run")[prefix_count:]
    emitted = []
    for message in suffix:
        if message["sender_role"] == "worker":
            assert emitted.pop(0) == message
        else:
            driver.restored_protocol.accept(message)
            emitted.extend(driver.process(message, driver.restored_protocol))
    assert emitted == [] and result.resume_state.bar_index == 2
    assert engine.position.realized_profit == 20 and engine.equity == 1060
    assert driver.session.export_state() == actual.session.export_state()
    assert driver.last_commit == suffix[-1]
    objects = identities(driver)
    state = driver.session.export_state()
    assert not driver.activate_job_restore(staged)
    assert driver.session.export_state() == state
    assert all(a is b for a, b in zip(objects, identities(driver), strict=True))


@pytest.mark.parametrize("mutation", ["generation", "ack", "native", "artifact", "late-protocol"])
def test_whole_job_corruption_refuses_before_worker_mutation(compiled, cut, mutation):
    _, wire = cut
    driver = receiver(compiled)
    payload = decode_job_checkpoint(wire)
    if mutation == "generation":
        payload["worker_generation"] = 2
    elif mutation == "ack":
        payload["last_acknowledged_frame"] = payload["output_sequence"]
    elif mutation == "native":
        payload["native_checkpoint"] = "e30="
    elif mutation == "artifact":
        payload["artifacts"][next(reversed(payload["artifacts"]))] = "bnVsbA=="
    else:
        message = payload["protocol_messages"][-1]
        message["body"]["recalc_iteration"] += 1
        payload["protocol_messages"][-1] = seal_content_hash(
            message, schema_id="openpine.worker.protocol.v2"
        )
    before, objects = driver.session.export_state(), identities(driver)
    with pytest.raises(
        (ValueError, ResumeUnsupportedError, WorkerProtocolError, WorkerProtocolSemanticError)
    ):
        driver.prepare_job_restore(
            encode_job_checkpoint(payload), job_id="job", worker_generation=1
        )
    assert driver.session.export_state() == before
    assert all(a is b for a, b in zip(objects, identities(driver), strict=True))
    assert driver.last_commit is driver.restored_protocol is None


def test_changed_foreign_provisional_and_tampered_tokens_refuse(compiled, cut):
    _, wire = cut
    driver = receiver(compiled)
    staged = driver.prepare_job_restore(wire, job_id="job", worker_generation=1)
    other = receiver(compiled)
    with pytest.raises(ValueError, match="another receiver"):
        other.activate_job_restore(staged)
    driver.current_bar = {"open": "100"}
    with pytest.raises(ValueError, match="provisional"):
        driver.activate_job_restore(staged)
    driver.current_bar = None
    with pytest.raises((ValueError, ResumeUnsupportedError, WorkerProtocolError)):
        driver.activate_job_restore(replace(staged, wire=wire[:-1]))
    assert driver.last_commit is None and driver.restored_protocol is None
    assert driver.activate_job_restore(staged)
    changed = replace(staged, checkpoint_hash="sha256:" + "a" * 64)
    before = driver.session.export_state()
    with pytest.raises(ValueError, match="changed after"):
        driver.activate_job_restore(changed)
    assert driver.session.export_state() == before


def test_uncommitted_protocol_prefix_has_defined_refusal(compiled, cut):
    _, wire = cut
    payload = decode_job_checkpoint(wire)
    payload["protocol_messages"].pop()
    driver = receiver(compiled)
    with pytest.raises(
        (ValueError, ResumeUnsupportedError, WorkerProtocolError, WorkerProtocolSemanticError)
    ):
        driver.prepare_job_restore(
            encode_job_checkpoint(payload), job_id="job", worker_generation=1
        )


def test_prepared_old_generation_cannot_replace_a_newer_identical_cut(compiled, cut):
    _, wire = cut
    driver = receiver(compiled)
    first = driver.prepare_job_restore(wire, job_id="job", worker_generation=1)
    assert driver.activate_job_restore(first)
    old = driver.prepare_job_restore(wire, job_id="job", worker_generation=1)
    payload = decode_job_checkpoint(wire)
    payload["worker_generation"] = 2
    newer = driver.prepare_job_restore(
        encode_job_checkpoint(payload), job_id="job", worker_generation=2
    )
    assert driver.activate_job_restore(newer)
    before, objects = driver.session.export_state(), identities(driver)
    with pytest.raises(ValueError, match="stale worker restore generation"):
        driver.activate_job_restore(old)
    assert driver.session.export_state() == before
    assert all(a is b for a, b in zip(objects, identities(driver), strict=True))
    assert driver._active_restore == ("job", 2, newer.checkpoint_hash)


def test_local_restore_does_not_advertise_or_enable_wire_resume():
    assert WORKER_CAPABILITIES == ("closed_bar",)
    with pytest.raises(ValueError, match="unsupported"):
        validate_requested_capabilities(["closed_bar", "checkpoint_v1"])
