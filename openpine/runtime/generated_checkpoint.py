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
from pinelib.state.checkpoint import canonical_json, is_canonical_sha256, sha
from pinelib.state.digest import AppendOnlyHistory

from openpine.runtime.rc6_config import serialize_engine_config
from openpine.runtime.rc6_lifecycle import ExecutionCursor

SCHEMA = "openpine.rc6.generated_checkpoint.v2"
JOURNAL_DOMAIN = "openpine.generated-callback-receipts.v1"
_FIELDS = {
    "schema_id",
    "identity",
    "runtime",
    "last_event",
    "intent_sequence",
    "content_hash",
    "callback_receipts",
    "callback_receipts_identity",
}
_RECEIPT_FIELDS = {"runtime_sequence", "event", "intent_count", "intent_batch_hash"}


def validate_receipts(data, identity, runtime):
    """Rebuild output cursors from receipts and cross-check committed runtime frames.

    Receipts prove internal consistency, not authenticity against a malicious party
    able to replace the runtime, journal and every checksum together.
    """
    if not isinstance(data, list):
        raise ValueError("generated checkpoint callback receipts must be an array")
    journal = AppendOnlyHistory(JOURNAL_DOMAIN)
    for item in data:
        if (
            not isinstance(item, dict)
            or set(item) != _RECEIPT_FIELDS
            or type(item["runtime_sequence"]) is not int
            or item["runtime_sequence"] < 0
            or type(item["intent_count"]) is not int
            or item["intent_count"] < 0
            or not is_canonical_sha256(item["intent_batch_hash"])
        ):
            raise ValueError("generated checkpoint callback receipt is invalid")
        journal.append(item)
    if journal.identity() != identity:
        raise ValueError("generated checkpoint callback receipt digest mismatch")
    cursor = ExecutionCursor()
    count = 0
    ordinal = 0
    bar_ordinal = 0
    mode = None
    time_storage = runtime.series.get("time")
    times = () if time_storage is None else time_storage.committed
    from pinelib.runtime.metadata import BarValues

    for frame in runtime.transcript.entries:
        if frame["phase"] == "BAR_COMMIT":
            if mode != "event":
                raise ValueError("generated checkpoint commit lacks its callback receipt")
            cursor.finish(frame["bar_index"])
            bar_ordinal += 1
            continue
        if ordinal >= len(journal):
            raise ValueError("generated checkpoint is missing callback receipts")
        item = journal[ordinal]
        ordinal += 1
        if item["runtime_sequence"] != frame["sequence"]:
            raise ValueError("generated checkpoint receipt sequence differs from runtime")
        count += item["intent_count"]
        new_mode = "direct" if item["event"] is None else "event"
        if mode is not None and mode != new_mode:
            raise ValueError("generated checkpoint mixes direct and event callbacks")
        mode = new_mode
        if new_mode == "event":
            event = ExecutionEvent.from_dict(item["event"])
            if any(
                getattr(event, key) != frame[key]
                for key in ("phase", "bar_index", "tick_index", "realtime", "final_tick")
            ):
                raise ValueError("generated checkpoint callback flags differ from runtime")
            if bar_ordinal >= len(times) or event.bar_open_time_utc_ms != times[bar_ordinal]:
                raise ValueError("generated checkpoint callback time differs from committed data")
            # Replay the same causal ordering checks as live execution, including
            # recalculation ordinals, dataset bounds and per-bar commit boundaries.
            values = BarValues(
                **{
                    key: runtime.series[key].committed[bar_ordinal]
                    for key in ("open", "high", "low", "close", "volume", "time", "time_close")
                }
            )
            cursor.validate(event, values)
            cursor.accept(event)
        else:
            bar_ordinal += 1
    if ordinal != len(journal) or cursor.open_bar is not None or bar_ordinal != len(times):
        raise ValueError("generated checkpoint has extra or provisional callback receipts")
    return journal, cursor, count


class GeneratedCheckpointMixin:
    """Shared by the native generated-script session in both execution modes."""

    def _record_callback(self, event, intents) -> None:
        self._callback_receipts.append(
            {
                "runtime_sequence": self.session.sequence,
                "event": None if event is None else event.to_dict(),
                "intent_count": len(intents),
                "intent_batch_hash": sha(list(intents)),
            }
        )

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
            "callback_receipts": list(self._callback_receipts),
            "callback_receipts_identity": self._callback_receipts.identity(),
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
        journal, cursor, derived_sequence = validate_receipts(
            state["callback_receipts"], state["callback_receipts_identity"], candidate
        )
        if sequence != derived_sequence:
            raise ValueError("generated checkpoint intent sequence differs from callback receipts")
        if event != cursor.last:
            raise ValueError("generated checkpoint last event differs from callback receipts")
        # Replace only after complete validation. Bad input leaves the current
        # runtime, caches and cursors unchanged, including on partial decode.
        self.session = candidate
        self.execution_cursor = cursor
        self._callback_receipts = journal
        self._intent_sequence = sequence
