"""Construct and validate sealed Worker Protocol 2.2 messages."""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from typing import Any

from openpine_contracts import (
    seal_content_hash,
    validate_payload,
    validate_worker_protocol_sequence,
    verify_content_hash,
)

_SCHEMA_ID = "openpine.worker.protocol.v2"
_COMPONENT_BY_KIND = {
    "HELLO": "openpine",
    "LOAD_ARTIFACT": "openpine",
    "INIT_RUN": "openpine",
    "BAR_BEGIN": "openpine",
    "INTENT_BATCH": "pinelib",
    "BROKER_EVENT_BATCH": "backtest_engine",
    "RECALC_REQUEST": "backtest_engine",
    "RECALC_RESULT": "pinelib",
    "BAR_COMMIT": "backtest_engine",
    "CHECKPOINT": "openpine",
    "RESTORE": "openpine",
    "FINALIZE": "openpine",
    "ABORT": "openpine",
}
_ROLE_BY_KIND = {
    "HELLO": "worker",
    "LOAD_ARTIFACT": "parent",
    "INIT_RUN": "parent",
    "BAR_BEGIN": "parent",
    "INTENT_BATCH": "worker",
    "BROKER_EVENT_BATCH": "engine",
    "RECALC_REQUEST": "engine",
    "RECALC_RESULT": "worker",
    "BAR_COMMIT": "engine",
    "CHECKPOINT": "parent",
    "RESTORE": "parent",
    "FINALIZE": "parent",
    "ABORT": "parent",
}
_ALLOWED_AFTER = {
    "HELLO": {"LOAD_ARTIFACT", "ABORT"},
    "LOAD_ARTIFACT": {"INIT_RUN", "ABORT"},
    "INIT_RUN": {"BAR_BEGIN", "RESTORE", "FINALIZE", "ABORT"},
    "BAR_BEGIN": {"INTENT_BATCH", "ABORT"},
    "INTENT_BATCH": {"BROKER_EVENT_BATCH", "BAR_COMMIT", "ABORT"},
    "BROKER_EVENT_BATCH": {"RECALC_REQUEST", "BAR_COMMIT", "ABORT"},
    "RECALC_REQUEST": {"RECALC_RESULT", "ABORT"},
    "RECALC_RESULT": {"INTENT_BATCH", "ABORT"},
    "BAR_COMMIT": {"BAR_BEGIN", "CHECKPOINT", "FINALIZE", "ABORT"},
    "CHECKPOINT": {"BAR_BEGIN", "RESTORE", "FINALIZE", "ABORT"},
    "RESTORE": {"BAR_BEGIN", "FINALIZE", "ABORT"},
    "FINALIZE": set(),
    "ABORT": set(),
}


class WorkerProtocolError(RuntimeError):
    pass


def _semver(value: object) -> str:
    if not isinstance(value, str) or not value:
        raise WorkerProtocolError("component version is missing")
    if "rc" in value and "-rc." not in value:
        base, marker, rc = value.partition("rc")
        if marker and base and rc.isdigit():
            return f"{base}-rc.{rc}"
    return value


class WorkerProtocolTranscript:
    """Identity-stable builder that validates every message and transcript prefix."""

    def __init__(self, execution_context: Mapping[str, Any]) -> None:
        context = deepcopy(dict(execution_context))
        validate_payload("openpine.execution_context.v1", context)
        if not verify_content_hash(context, schema_id="openpine.execution_context.v1"):
            raise WorkerProtocolError("execution context content hash is invalid")
        self.execution_context = context
        self._messages: list[dict[str, Any]] = []
        self._components = self._component_identities(context)
        for field in ("session_id", "run_id", "stack_manifest_hash"):
            if not isinstance(context.get(field), str) or not context[field]:
                raise WorkerProtocolError(f"execution context {field} is missing")

    @staticmethod
    def _component_identities(
        context: Mapping[str, Any],
    ) -> dict[str, tuple[str, str]]:
        commits = context.get("producer_commits")
        wheels = context.get("wheel_identities")
        if not isinstance(commits, Mapping) or not isinstance(wheels, list):
            raise WorkerProtocolError("execution context component identity is missing")
        versions: dict[str, str] = {}
        for row in wheels:
            if isinstance(row, Mapping) and isinstance(row.get("name"), str):
                versions[str(row["name"])] = _semver(row.get("version"))
        identities: dict[str, tuple[str, str]] = {}
        for component in set(_COMPONENT_BY_KIND.values()):
            version = versions.get(component)
            commit = commits.get(component)
            if not version or not isinstance(commit, str) or len(commit) != 40:
                raise WorkerProtocolError(
                    f"execution context identity for {component} is missing"
                )
            identities[component] = (version, commit)
        return identities

    @property
    def messages(self) -> tuple[dict[str, Any], ...]:
        return tuple(deepcopy(self._messages))

    @property
    def last_message_id(self) -> str | None:
        if not self._messages:
            return None
        return str(self._messages[-1]["message_id"])

    def _check_transition(self, kind: str) -> None:
        if not self._messages and kind != "HELLO":
            raise WorkerProtocolError("worker protocol must start with HELLO")
        if self._messages:
            previous_kind = str(self._messages[-1]["kind"])
            if kind not in _ALLOWED_AFTER[previous_kind]:
                raise WorkerProtocolError(
                    f"invalid worker protocol transition: {previous_kind} -> {kind}"
                )

    def accept(self, message: Mapping[str, Any]) -> dict[str, Any]:
        candidate_message = deepcopy(dict(message))
        validate_payload(_SCHEMA_ID, candidate_message)
        if not verify_content_hash(candidate_message, schema_id=_SCHEMA_ID):
            raise WorkerProtocolError("worker protocol message content hash is invalid")
        kind = str(candidate_message["kind"])
        component = _COMPONENT_BY_KIND.get(kind)
        if component is None:
            raise WorkerProtocolError(f"unsupported worker message kind: {kind}")
        self._check_transition(kind)
        version, commit = self._components[component]
        expected = {
            "producer": component,
            "producer_version": version,
            "producer_commit": commit,
            "stack_id": self.execution_context["stack_manifest_hash"],
            "session_id": self.execution_context["session_id"],
            "run_id": self.execution_context["run_id"],
            "sequence": len(self._messages),
            "correlation_id": self.execution_context["run_id"],
            "causation_id": self.last_message_id,
            "sender_role": _ROLE_BY_KIND[kind],
        }
        for field, value in expected.items():
            if candidate_message.get(field) != value:
                raise WorkerProtocolError(f"worker protocol {field} mismatch")
        candidate = [*self._messages, candidate_message]
        if kind in {"FINALIZE", "ABORT"}:
            validate_worker_protocol_sequence(candidate)
        self._messages.append(candidate_message)
        return deepcopy(candidate_message)

    def append(
        self,
        kind: str,
        body: Mapping[str, Any],
        *,
        created_at_utc_ms: int,
        sender_role: str | None = None,
    ) -> dict[str, Any]:
        component = _COMPONENT_BY_KIND.get(kind)
        if component is None:
            raise WorkerProtocolError(f"unsupported worker message kind: {kind}")
        role = sender_role or _ROLE_BY_KIND[kind]
        expected_role = _ROLE_BY_KIND[kind]
        if role != expected_role and kind != "ABORT":
            raise WorkerProtocolError(f"invalid sender role for {kind}")
        self._check_transition(kind)
        version, commit = self._components[component]
        sequence = len(self._messages)
        session_id = str(self.execution_context["session_id"])
        run_id = str(self.execution_context["run_id"])
        payload = {
            "schema_id": _SCHEMA_ID,
            "schema_version": "2.3.0",
            "producer": component,
            "producer_version": version,
            "producer_commit": commit,
            "stack_id": self.execution_context["stack_manifest_hash"],
            "created_at_utc_ms": int(created_at_utc_ms),
            "serializer_id": "openpine.canonical.json.v1",
            "content_hash_alg": "sha256",
            "message_id": f"{session_id}:{sequence}:{kind}",
            "sender_role": role,
            "session_id": session_id,
            "run_id": run_id,
            "sequence": sequence,
            "correlation_id": run_id,
            "causation_id": self.last_message_id,
            "kind": kind,
            "body": deepcopy(dict(body)),
        }
        sealed = seal_content_hash(payload, schema_id=_SCHEMA_ID)
        validate_payload(_SCHEMA_ID, sealed)
        candidate = [*self._messages, sealed]
        if kind in {"FINALIZE", "ABORT"}:
            validate_worker_protocol_sequence(candidate)
        self._messages.append(sealed)
        return deepcopy(sealed)

    def validate(self) -> None:
        validate_worker_protocol_sequence(self._messages)


__all__ = ["WorkerProtocolError", "WorkerProtocolTranscript"]
