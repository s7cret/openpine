from __future__ import annotations

from copy import deepcopy

import pytest
from openpine_contracts import validate_payload, validate_worker_protocol_sequence

from openpine.runtime.worker_protocol import (
    WorkerProtocolError,
    WorkerProtocolTranscript,
)
from tests.admission_helpers import make_sealed_artifact
from tests.rc4_fixtures import execution_context

HASH_A = "sha256:" + "a" * 64


def _bootstrap(transcript: WorkerProtocolTranscript) -> None:
    artifact = make_sealed_artifact(python_code="VALUE = 1\n")["generated_artifact"]
    transcript.append(
        "HELLO",
        {
            "worker_id": "worker-1",
            "protocol_version": "2.3.0",
            "capabilities": ["closed_bar", "checkpoint_v1"],
        },
        created_at_utc_ms=0,
    )
    transcript.append(
        "LOAD_ARTIFACT",
        {
            "artifact_hash": artifact["content_hash"],
            "module_hash": artifact["emitted_module_hash"],
            "entrypoint_module": artifact["entrypoint_module"],
            "entrypoint_class": artifact["entrypoint_class"],
        },
        created_at_utc_ms=0,
    )
    transcript.append(
        "INIT_RUN",
        {
            "run_id": transcript.execution_context["run_id"],
            "run_hash": HASH_A,
            "execution_context_hash": transcript.execution_context["content_hash"],
            "execution_context": transcript.execution_context,
            "semantic_profile": "strict_5x",
            "capabilities": ["closed_bar", "checkpoint_v1"],
        },
        created_at_utc_ms=0,
    )


def test_transcript_builds_full_sealed_identity_stable_protocol_messages() -> None:
    transcript = WorkerProtocolTranscript(execution_context())
    _bootstrap(transcript)
    transcript.append(
        "ABORT",
        {
            "run_id": transcript.execution_context["run_id"],
            "error_code": "TEST_STOP",
            "reason": "done",
        },
        created_at_utc_ms=1,
    )

    messages = transcript.messages
    validate_worker_protocol_sequence(messages)
    assert [message["sequence"] for message in messages] == list(range(4))
    assert [message["causation_id"] for message in messages] == [
        None,
        messages[0]["message_id"],
        messages[1]["message_id"],
        messages[2]["message_id"],
    ]
    assert messages[0]["producer"] == "openpine"
    assert messages[0]["producer_version"] == "5.0.0-rc.5"
    for message in messages:
        validate_payload("openpine.worker.protocol.v2", message)
        assert message["schema_id"] == "openpine.worker.protocol.v2"
        assert message["content_hash"].startswith("sha256:")


def test_transcript_rejects_tampered_execution_context_before_any_message() -> None:
    context = execution_context()
    context["run_id"] = "foreign-run"
    with pytest.raises(WorkerProtocolError, match="content hash"):
        WorkerProtocolTranscript(context)


def test_transcript_rejects_sender_role_before_append() -> None:
    transcript = WorkerProtocolTranscript(execution_context())
    before = deepcopy(transcript.messages)
    with pytest.raises(WorkerProtocolError, match="sender role"):
        transcript.append(
            "HELLO",
            {
                "worker_id": "worker-1",
                "protocol_version": "2.3.0",
                "capabilities": [],
            },
            created_at_utc_ms=0,
            sender_role="parent",
        )
    assert transcript.messages == tuple(before)


def test_parent_and_worker_accept_the_same_wire_messages_fail_closed() -> None:
    parent = WorkerProtocolTranscript(execution_context())
    worker = WorkerProtocolTranscript(execution_context())
    hello = worker.append(
        "HELLO",
        {
            "worker_id": "worker-1",
            "protocol_version": "2.3.0",
            "capabilities": ["closed_bar"],
        },
        created_at_utc_ms=0,
    )
    assert parent.accept(hello) == hello

    tampered = deepcopy(hello)
    tampered["message_id"] = "foreign"
    with pytest.raises(WorkerProtocolError):
        WorkerProtocolTranscript(execution_context()).accept(tampered)


@pytest.mark.parametrize(
    ("role", "producer"),
    (("parent", "openpine"), ("worker", "openpine"), ("engine", "backtest_engine")),
)
def test_abort_role_and_producer_match_the_contract(role: str, producer: str) -> None:
    sender = WorkerProtocolTranscript(execution_context())
    receiver = WorkerProtocolTranscript(execution_context())
    hello = sender.append(
        "HELLO",
        {
            "worker_id": "worker-1",
            "protocol_version": "2.3.0",
            "capabilities": ["closed_bar"],
        },
        created_at_utc_ms=0,
    )
    receiver.accept(hello)
    abort = sender.append(
        "ABORT",
        {
            "run_id": sender.execution_context["run_id"],
            "error_code": "TEST_ABORT",
            "reason": role,
        },
        created_at_utc_ms=1,
        sender_role=role,
    )

    assert abort["producer"] == producer
    assert receiver.accept(abort) == abort
    validate_worker_protocol_sequence(receiver.messages)
