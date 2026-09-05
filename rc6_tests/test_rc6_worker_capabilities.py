"""An isolated worker must not advertise or negotiate unimplemented resume."""

import io
import json

import pytest

from openpine.runtime.isolated_run import IsolatedRunError, run_isolated_artifact
from openpine.runtime.rc6_config import serialize_engine_config
from openpine.runtime.rc6_worker_runtime import RC6WorkerProtocol, run_bulk, run_interactive
from openpine.runtime.worker_capabilities import (
    WORKER_CAPABILITIES,
    require_worker_capabilities,
    validate_requested_capabilities,
)
from rc6_tests.test_rc6_bulk_execution import bulk_case


@pytest.mark.parametrize(
    "value", [["checkpoint_v1"], ["unknown"], [True], "closed_bar", ["closed_bar", "closed_bar"]]
)
def test_unimplemented_or_malformed_protocol_request_is_rejected(value):
    with pytest.raises(ValueError):
        validate_requested_capabilities(value)


@pytest.mark.parametrize(
    "value", [None, [], ["checkpoint_v1"], [True], ["closed_bar", "closed_bar"]]
)
def test_host_rejects_missing_required_worker_advertisement(value):
    with pytest.raises(ValueError):
        require_worker_capabilities(value)


def test_supported_protocol_set_is_explicit():
    assert WORKER_CAPABILITIES == ("closed_bar",)
    validate_requested_capabilities(list(WORKER_CAPABILITIES))
    require_worker_capabilities(list(WORKER_CAPABILITIES))


@pytest.mark.parametrize("runner", [run_bulk, run_interactive])
def test_actual_handshake_rejects_checkpoint_before_bar_execution(monkeypatch, runner):
    compiled, context, config = bulk_case.__wrapped__()
    generated = compiled.generated_artifact
    request = dict(
        generated_artifact=generated,
        execution_context=context,
        source=compiled.python_code,
        engine_config=serialize_engine_config(config, "strict_5x"),
    )
    producer = RC6WorkerProtocol(context)
    producer.append(
        "HELLO",
        dict(
            worker_id=context["session_id"],
            protocol_version="2.3.0",
            capabilities=list(WORKER_CAPABILITIES),
        ),
        0,
    )
    load = producer.append(
        "LOAD_ARTIFACT",
        dict(
            artifact_hash=generated["content_hash"],
            module_hash=generated["emitted_module_hash"],
            entrypoint_module=generated["entrypoint"]["module"],
            entrypoint_class="GeneratedScript",
        ),
        0,
    )
    init = producer.append(
        "INIT_RUN",
        dict(
            run_id=context["run_id"],
            run_hash="sha256:" + "1" * 64,
            execution_context_hash=context["content_hash"],
            execution_context=context,
            semantic_profile="strict_5x",
            capabilities=["closed_bar", "checkpoint_v1"],
        ),
        0,
    )
    output = io.StringIO()
    monkeypatch.setattr("sys.stdin", io.StringIO(json.dumps(load) + "\n" + json.dumps(init) + "\n"))
    monkeypatch.setattr("sys.stdout", output)
    with pytest.raises(ValueError, match="unsupported worker protocol capabilities"):
        runner(request, RC6WorkerProtocol(context))
    messages = [json.loads(line) for line in output.getvalue().splitlines()]
    assert len(messages) == 1 and messages[0]["kind"] == "HELLO"
    assert messages[0]["body"]["capabilities"] == ["closed_bar"]


def test_outer_resume_remains_an_early_explicit_error():
    with pytest.raises(IsolatedRunError, match="RESUME_UNSUPPORTED_FOR_WORKER_PROTOCOL"):
        run_isolated_artifact(b"not even compiled", bars=[], config=object(), resume_state={})
