"""Complete generated-session checkpoints at committed bar boundaries.

These include Pine state, request contexts and callback/intent cursors, not a
complete broker/IPC resume protocol. Checksums are not signatures or TV proofs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict
from typing import Any

from openpine_contracts import ExecutionEvent
from pinelib import RuntimeSession
from pinelib.state.checkpoint import canonical_json, sha

from openpine.runtime.rc6_config import serialize_engine_config
from openpine.runtime.rc6_lifecycle import ExecutionCursor

SCHEMA = "openpine.rc6.generated_checkpoint.v1"
_FIELDS = {"schema_id", "identity", "runtime", "last_event", "intent_sequence", "content_hash"}


class GeneratedCheckpointMixin:
    """Shared by the native generated-script session in both execution modes."""

    def _checkpoint_identity(self) -> dict[str, Any]:
        config = serialize_engine_config(self.intent_config, self.identity.semantic_profile)
        return {
            "artifact_hash": self._artifact_hash,
            "runtime_identity": self.session.identity_hash,
            "run": asdict(self.identity),
            "producer_commit": self.producer_commit,
            "effective_config_hash": config["effective_config_hash"],
            "inputs_hash": self.inputs.identity_hash,
            "host_surface_hash": self.strategy_host["surface_hash"],
        }

    def export_state(self) -> dict[str, Any]:
        if self.execution_cursor.open_bar is not None:
            raise ValueError("cannot export a generated session with an uncommitted bar")
        body = {
            "schema_id": SCHEMA,
            "identity": self._checkpoint_identity(),
            "runtime": self.session.checkpoint().to_dict(),
            "last_event": None
            if self.execution_cursor.last is None
            else self.execution_cursor.last.to_dict(),
            "intent_sequence": self._intent_sequence,
        }
        body["content_hash"] = sha(body)
        if len(canonical_json(body)) > self.session.policies.resource.max_checkpoint_bytes:
            raise ValueError("generated session checkpoint exceeds the configured limit")
        return body

    def restore_state(self, state: Mapping[str, Any]) -> None:
        if self.execution_cursor.open_bar is not None:
            raise ValueError("cannot restore a generated session with an uncommitted bar")
        self.session.checkpoint()
        if not isinstance(state, Mapping) or set(state) != _FIELDS or state["schema_id"] != SCHEMA:
            raise ValueError("generated session checkpoint schema mismatch")
        if len(canonical_json(state)) > self.session.policies.resource.max_checkpoint_bytes:
            raise ValueError("generated session checkpoint exceeds the configured limit")
        body = {key: value for key, value in state.items() if key != "content_hash"}
        if state["content_hash"] != sha(body):
            raise ValueError("generated session checkpoint hash mismatch")
        if state["identity"] != self._checkpoint_identity():
            raise ValueError("generated session checkpoint identity mismatch")
        sequence = state["intent_sequence"]
        if type(sequence) is not int or sequence < 0:
            raise ValueError("generated checkpoint intent sequence is invalid")
        event = (
            None if state["last_event"] is None else ExecutionEvent.from_dict(state["last_event"])
        )
        previous = self.session
        candidate = RuntimeSession(
            previous.language,
            previous.policies,
            inputs=previous.inputs,
            instrument=previous.instrument,
            timeframe=previous.timeframe,
            request_provider=previous.requests.provider,
        )
        candidate.commit_full_identity = previous.commit_full_identity
        candidate.restore(state["runtime"])
        entries = candidate.transcript.entries
        if event is not None:
            if (
                not entries
                or entries[-1]["phase"] != "BAR_COMMIT"
                or entries[-1]["bar_index"] != event.bar_index
            ):
                raise ValueError("generated checkpoint cursor differs from committed runtime")
            callbacks = [entry for entry in entries if entry["phase"] != "BAR_COMMIT"]
            if (
                len(callbacks) != event.sequence + 1
                or callbacks[-1]["bar_index"] != event.bar_index
            ):
                raise ValueError("generated checkpoint callback sequence differs from runtime")
        elif any(entry["phase"] == "BAR_COMMIT" for entry in entries):
            raise ValueError("generated checkpoint is missing its deferred execution cursor")
        if candidate.sequence == -1 and sequence != 0:
            raise ValueError("empty generated checkpoint cannot contain committed intents")
        # Replace only after complete validation. Bad input leaves the current
        # runtime, caches and cursors unchanged, including on partial decode.
        self.session = candidate
        self.execution_cursor = ExecutionCursor(last=event)
        self._intent_sequence = sequence
