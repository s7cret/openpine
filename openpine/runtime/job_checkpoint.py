"""A bounded committed job envelope over the existing native and Pine codecs."""

from __future__ import annotations

import base64
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, TYPE_CHECKING

from backtest_engine import JsonResumeStateSerializer
from backtest_engine.core.resume_json import _json_depth
from backtest_engine.core.state_snapshot import BrokerSnapshot, JsonStateSerializer
from backtest_engine.errors import ResumeUnsupportedError
from openpine_contracts import canonical_dumps, validate_payload, verify_content_hash
from pinelib.state.checkpoint import sha

from openpine.runtime.worker_protocol import WorkerProtocolTranscript

if TYPE_CHECKING:
    from openpine.runtime.rc6_worker_runtime import RC6GeneratedScriptSession

SCHEMA = "openpine.committed-job.v1"
MAX_BYTES = 16 * 1024 * 1024
_FIELDS = {
    "schema",
    "job_id",
    "run_id",
    "execution_context_hash",
    "worker_generation",
    "callback_sequence",
    "intent_sequence",
    "output_sequence",
    "last_acknowledged_frame",
    "committed_sequence",
    "recovery_boundary",
    "native_checkpoint",
    "protocol_messages",
    "artifacts",
    "content_hash",
}


@dataclass(frozen=True, slots=True)
class PreparedJobCheckpoint:
    """Detached admitted transport. Native.run still owns config/data admission."""

    payload: dict[str, Any]
    native_bytes: bytes
    transcript: WorkerProtocolTranscript


def encode_job_checkpoint(payload: Mapping[str, Any]) -> bytes:
    body = {key: value for key, value in payload.items() if key != "content_hash"}
    wire = canonical_dumps({**body, "content_hash": sha(body)}).encode("utf-8")
    if len(wire) > MAX_BYTES:
        raise ResumeUnsupportedError("job checkpoint exceeds its byte budget")
    return wire


def decode_job_checkpoint(wire: bytes) -> dict[str, Any]:
    if type(wire) is not bytes or not wire or len(wire) > MAX_BYTES:
        raise ResumeUnsupportedError("job checkpoint exceeds its byte budget")
    _json_depth(wire, 64)
    try:
        value = JsonStateSerializer().loads(wire)
    except (UnicodeError, ValueError, RecursionError) as error:
        raise ResumeUnsupportedError("job checkpoint JSON is invalid") from error
    if type(value) is not dict or set(value) != _FIELDS or value["schema"] != SCHEMA:
        raise ResumeUnsupportedError("job checkpoint schema/fields mismatch")
    if value["content_hash"] != sha({k: v for k, v in value.items() if k != "content_hash"}):
        raise ResumeUnsupportedError("job checkpoint content hash mismatch")
    for name in ("job_id", "run_id", "execution_context_hash"):
        if type(value[name]) is not str or not value[name]:
            raise ResumeUnsupportedError("job checkpoint identity is invalid")
    for name in (
        "worker_generation",
        "callback_sequence",
        "intent_sequence",
        "output_sequence",
        "committed_sequence",
        "last_acknowledged_frame",
    ):
        if type(value[name]) is not int or value[name] < (
            -1 if name == "last_acknowledged_frame" else 0
        ):
            raise ResumeUnsupportedError("job checkpoint sequence is invalid: " + name)
    if value["worker_generation"] < 1:
        raise ResumeUnsupportedError("job checkpoint generation is invalid")
    if value["recovery_boundary"] != "committed-parent-bar":
        raise ResumeUnsupportedError("job checkpoint recovery boundary is unsupported")
    if (
        value["output_sequence"] != value["committed_sequence"] + 1
        or value["last_acknowledged_frame"] > value["committed_sequence"]
    ):
        raise ResumeUnsupportedError("job checkpoint output/ack cursor mismatch")
    if type(value["protocol_messages"]) is not list or type(value["artifacts"]) is not dict:
        raise ResumeUnsupportedError("job checkpoint transcript/artifacts are invalid")
    return value


def _native_bytes(value: Any) -> bytes:
    if type(value) is not str:
        raise ResumeUnsupportedError("job checkpoint native transport is invalid")
    try:
        return base64.b64decode(value, validate=True)
    except ValueError as error:
        raise ResumeUnsupportedError("job checkpoint native transport is invalid") from error


def prepare_job_checkpoint(
    wire: bytes,
    *,
    job_id: str,
    context: Mapping[str, Any],
    session: RC6GeneratedScriptSession,
    worker_generation: int | None = None,
) -> PreparedJobCheckpoint:
    """Admit every owner, callback and artifact without changing a receiver."""
    payload = decode_job_checkpoint(wire)
    expected: dict[str, Any] = {
        "job_id": job_id,
        "run_id": context["run_id"],
        "execution_context_hash": context["content_hash"],
    }
    if worker_generation is not None:
        expected["worker_generation"] = worker_generation
    if any(payload[key] != value for key, value in expected.items()):
        raise ResumeUnsupportedError("job checkpoint execution identity/generation mismatch")
    messages = payload["protocol_messages"]
    transcript = WorkerProtocolTranscript.from_committed_messages(context, messages)
    commit = messages[-1]
    if (
        payload["output_sequence"] != len(messages)
        or commit["sequence"] != payload["committed_sequence"]
    ):
        raise ResumeUnsupportedError("job checkpoint transcript cursor mismatch")
    native = _native_bytes(payload["native_checkpoint"])
    state = JsonResumeStateSerializer().loads(native)
    if not isinstance(state.strategy_state, dict):
        raise ResumeUnsupportedError("job checkpoint requires the generated native owner")
    owner = session.prepare_restore(state.strategy_state)
    from openpine.runtime.generated_backtest import _validate_fill_receipts

    _validate_fill_receipts(
        owner, state, calc_on_order_fills=session.intent_config.calc_on_order_fills
    )
    last = owner.cursor.last
    if (
        last is None
        or last.realtime
        or not last.final_tick
        or state.bar_index != commit["body"]["bar_index"]
        or state.bar_index != last.bar_index
        or last.recalc_iteration != commit["body"]["recalc_iteration"]
        or payload["callback_sequence"] != last.sequence + 1
        or payload["intent_sequence"] != owner.intent_sequence
    ):
        raise ResumeUnsupportedError("job checkpoint native/Pine/protocol boundary mismatch")
    identity = session.identity
    for field in (
        "run_id",
        "strategy_id",
        "series_id",
        "instrument_id",
        "timeframe",
        "semantic_profile",
    ):
        if getattr(identity, field) != context[field]:
            raise ResumeUnsupportedError("job checkpoint Pine execution identity mismatch")
    if identity.stack_id != context["stack_manifest_hash"]:
        raise ResumeUnsupportedError("job checkpoint Pine stack identity mismatch")
    batches = [message for message in messages if message["kind"] == "INTENT_BATCH"]
    callbacks = [
        message for message in messages if message["kind"] in {"BAR_BEGIN", "RECALC_REQUEST"}
    ]
    if len(batches) != len(owner.receipts):
        raise ResumeUnsupportedError("job checkpoint callback count differs from Pine receipts")
    if len(callbacks) != len(batches):
        raise ResumeUnsupportedError("job checkpoint callback/intent transcript mismatch")
    for message, callback, receipt in zip(batches, callbacks, owner.receipts, strict=True):
        body, event = message["body"], receipt["event"]
        if (
            event is None
            or callback["body"].get("execution_event") != event
            or body["bar_index"] != event["bar_index"]
            or body["recalc_iteration"] != event["recalc_iteration"]
            or len(body["intents"]) != receipt["intent_count"]
            or sha(body["intents"]) != receipt["intent_batch_hash"]
        ):
            raise ResumeUnsupportedError("job checkpoint intents differ from Pine receipts")
    artifacts = payload["artifacts"]
    referenced: set[str] = set()
    for message in messages:
        if message["kind"] != "BAR_COMMIT":
            continue
        for field in ("state_ref", "broker_projection_ref"):
            ref = message["body"][field]
            key = ref["artifact_hash"]
            if key not in artifacts:
                raise ResumeUnsupportedError("job checkpoint is missing an immutable artifact")
            encoded = _native_bytes(artifacts[key])
            if len(encoded) != ref["size_bytes"] or ref["codec"] != "json":
                raise ResumeUnsupportedError("job checkpoint artifact size/codec mismatch")
            value = JsonStateSerializer().loads(encoded)
            if not isinstance(value, dict):
                raise ResumeUnsupportedError("job checkpoint artifact is not an object")
            schema_id = (
                "openpine.runtime.state.v1"
                if field == "state_ref"
                else "openpine.broker_projection.v1"
            )
            if ref["schema_id"] != schema_id:
                raise ResumeUnsupportedError("job checkpoint artifact owner/schema mismatch")
            expected_producer = next(
                row for row in context["wheel_identities"] if row["name"] == "backtest_engine"
            )
            from openpine.runtime.worker_protocol import _semver

            if (
                value.get("schema_id") != schema_id
                or value.get("producer") != "backtest_engine"
                or value.get("producer_commit") != context["producer_commits"]["backtest_engine"]
                or value.get("producer_version") != _semver(expected_producer["version"])
                or value.get("stack_id") != context["stack_manifest_hash"]
            ):
                raise ResumeUnsupportedError("job checkpoint artifact producer identity mismatch")
            if field == "broker_projection_ref":
                validate_payload(schema_id, value)
            else:
                fields = {
                    "schema_id",
                    "schema_version",
                    "producer",
                    "producer_version",
                    "producer_commit",
                    "stack_id",
                    "created_at_utc_ms",
                    "serializer_id",
                    "content_hash_alg",
                    "content_hash",
                    "run_id",
                    "strategy_id",
                    "series_id",
                    "instrument_id",
                    "timeframe",
                    "bar_index",
                    "bar_open_time_utc_ms",
                    "phase",
                    "recalc_iteration",
                    "strategy_state",
                }
                if set(value) != fields or value["schema_version"] != "1.0.0":
                    raise ResumeUnsupportedError(
                        "job checkpoint state artifact fields/version mismatch"
                    )
            if value["content_hash"] != key or not verify_content_hash(
                value, schema_id=ref["schema_id"]
            ):
                raise ResumeUnsupportedError("job checkpoint artifact hash mismatch")
            identity_fields: tuple[str, ...] = ("run_id", "series_id", "instrument_id")
            if field == "state_ref":
                identity_fields += ("strategy_id",)
            if any(value.get(k) != context[k] for k in identity_fields):
                raise ResumeUnsupportedError("job checkpoint artifact execution identity mismatch")
            if value["bar_index"] != message["body"]["bar_index"]:
                raise ResumeUnsupportedError("job checkpoint artifact boundary mismatch")
            if message is commit and field == "broker_projection_ref":
                broker = state.broker_state
                if not isinstance(broker, BrokerSnapshot):
                    raise ResumeUnsupportedError("job checkpoint requires the native broker owner")
                position = value["position"]
                if (
                    Decimal(value["cash"]) != Decimal(str(broker.cash))
                    or Decimal(value["equity"]) != Decimal(str(broker.equity))
                    or position["direction"] != broker.position.direction.upper()
                    or Decimal(position["qty"]) != Decimal(str(abs(broker.position.size)))
                    or (
                        position["avg_price"] is not None
                        and Decimal(position["avg_price"])
                        != Decimal(str(broker.position.avg_price))
                    )
                ):
                    raise ResumeUnsupportedError(
                        "job checkpoint protocol projection differs from native broker"
                    )
            if field == "state_ref":
                transport = value["strategy_state"]
                if (
                    not isinstance(transport, dict)
                    or set(transport) != {"schema", "codec", "inline_base64"}
                    or transport["schema"] != "openpine.generated-checkpoint.transport.v1"
                    or transport["codec"] != "json-v1"
                ):
                    raise ResumeUnsupportedError(
                        "job checkpoint protocol owner transport is invalid"
                    )
                graph = JsonStateSerializer().loads(_native_bytes(transport["inline_base64"]))
                if not isinstance(graph, dict):
                    raise ResumeUnsupportedError("job checkpoint protocol owner graph is invalid")
                session.prepare_restore(graph)
                if message is commit and graph != state.strategy_state:
                    raise ResumeUnsupportedError(
                        "job checkpoint protocol state differs from native Pine owner"
                    )
            referenced.add(key)
    if set(artifacts) != referenced:
        raise ResumeUnsupportedError("job checkpoint has unreferenced artifacts")
    return PreparedJobCheckpoint(payload, native, transcript)


def make_job_checkpoint(
    *,
    job_id: str,
    context: Mapping[str, Any],
    worker_generation: int,
    native_bytes: bytes,
    messages: Sequence[Mapping[str, Any]],
    artifacts: Mapping[str, bytes],
    last_acknowledged_frame: int,
) -> bytes:
    state = JsonResumeStateSerializer().loads(native_bytes)
    if not isinstance(state.strategy_state, dict):
        raise ResumeUnsupportedError("job checkpoint requires the generated native owner")
    receipts = state.strategy_state["callback_receipts"]
    return encode_job_checkpoint(
        {
            "schema": SCHEMA,
            "job_id": job_id,
            "run_id": context["run_id"],
            "execution_context_hash": context["content_hash"],
            "worker_generation": worker_generation,
            "callback_sequence": len(receipts),
            "intent_sequence": state.strategy_state["intent_sequence"],
            "output_sequence": len(messages),
            "committed_sequence": len(messages) - 1,
            "last_acknowledged_frame": last_acknowledged_frame,
            "recovery_boundary": "committed-parent-bar",
            "native_checkpoint": base64.b64encode(native_bytes).decode("ascii"),
            "protocol_messages": list(messages),
            "artifacts": {
                key: base64.b64encode(value).decode("ascii") for key, value in artifacts.items()
            },
        }
    )
