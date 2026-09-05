"""BULK_BACKTEST parent session: stream bars once, no per-bar IPC."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Iterator, Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from openpine.runtime.bulk_result import BulkResultError, BulkResultReceiver, result_identity
from openpine.runtime.rc6_config import resolve_engine_config
from openpine.runtime.progress import ProgressError, ProgressReporter
from openpine.runtime.isolated_worker import InteractiveWorkerSession, IsolatedWorkerError

BULK_MESSAGE_LIMIT_BYTES = 900_000


def chunk_bulk_frames(
    bars: Sequence[Mapping[str, Any]],
    *,
    max_bytes: int = BULK_MESSAGE_LIMIT_BYTES,
) -> Iterator[str]:
    """Serialize each envelope once and yield bounded JSON lines lazily.

    ASCII encoding makes the character count the byte count. The trusted sender
    writes these already serialized frames without a second full JSON traversal.
    """
    if type(max_bytes) is not int or max_bytes < 64:
        raise ValueError("bulk bar exceeds message limit")
    prefix, suffix = '{"kind":"BULK_BARS","bars":[', '],"last":false}'
    overhead = len(prefix) + len(suffix)
    current: list[str] = []
    current_size = 0
    for bar in bars:
        encoded = json.dumps(bar, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
        if len(encoded) + overhead > max_bytes:
            raise ValueError("bulk bar exceeds message limit")
        piece = len(encoded) + bool(current)
        if current and current_size + piece + overhead > max_bytes:
            yield prefix + ",".join(current) + suffix
            current, current_size = [], 0
            piece = len(encoded)
        current.append(encoded)
        current_size += piece
    yield prefix + ",".join(current) + '],"last":true}'


def hydrate_bulk_raw_result(payload: Mapping[str, Any]) -> Any:
    """Rebuild attribute-style engine result from the worker JSON payload."""

    def _ns(value: Any) -> Any:
        if isinstance(value, dict):
            return SimpleNamespace(**{key: _ns(item) for key, item in value.items()})
        if isinstance(value, list):
            return [_ns(item) for item in value]
        return value

    raw = payload.get("raw_result")
    if not isinstance(raw, Mapping):
        raise IsolatedWorkerError("bulk result payload is invalid")
    return _ns(dict(raw))


class BulkWorkerSession(InteractiveWorkerSession):
    """One sandbox job: LOAD/INIT, bar stream, PROGRESS/RESULT. Not INTERACTIVE."""

    def __init__(self, *args: Any, engine_config: Mapping[str, Any], **kwargs: Any) -> None:
        kwargs["bulk_backtest"] = True
        kwargs["engine_config"] = engine_config
        super().__init__(*args, **kwargs)

    def run_bars(
        self,
        envelopes: Sequence[Mapping[str, Any]],
        engine_config: Mapping[str, Any],
        *, progress_callback: Callable[[int, int], None] | None = None,
    ) -> dict[str, Any]:
        if not envelopes:
            raise IsolatedWorkerError("bulk backtest requires canonical bar envelopes")
        from openpine.runtime.inputs import input_evidence
        expected_inputs = input_evidence(self.input_registry)
        config = resolve_engine_config(engine_config, self.protocol.execution_context)
        identity = result_identity(self.protocol.execution_context, {
            **expected_inputs, "effective_config_hash": config.effective_config_hash,
        })
        progress = ProgressReporter(progress_callback, max_total=len(envelopes))
        for frame in chunk_bulk_frames(envelopes):
            self._write_serialized_json_line(frame)
        previous_timeout = self.timeout_s
        self.timeout_s = self.bulk_idle_timeout_s
        try:
            with BulkResultReceiver(identity) as receiver:
                while True:
                    message = self._read_message(require_protocol=False)
                    if message.get("kind") == "BULK_PROGRESS":
                        if receiver.chunks:
                            raise IsolatedWorkerError("progress after result transmission began")
                        if set(message) != {"kind", "bars_done", "bars_total"}:
                            raise IsolatedWorkerError("invalid bulk progress frame")
                        progress.report(message["bars_done"], message["bars_total"])
                        continue
                    payload = receiver.accept(message)
                    if payload is not None:
                        manifest = receiver.manifest
                        break
        except (BulkResultError, ProgressError) as exc:
            raise IsolatedWorkerError(str(exc)) from exc
        finally:
            self.timeout_s = previous_timeout
        raw = payload.get("raw_result")
        if not isinstance(raw, Mapping) or raw.get("status") != "completed":
            status = raw.get("status") if isinstance(raw, Mapping) else None
            raise IsolatedWorkerError(f"bulk engine did not complete: {status!r}")
        if raw.get("effective_config_hash") != identity["effective_config_hash"]:
            raise IsolatedWorkerError("bulk result effective-config identity mismatch")
        if any(raw.get(key) != value for key, value in expected_inputs.items()):
            raise IsolatedWorkerError("bulk result applied-input identity mismatch")
        processed = payload.get("bars_processed")
        received = payload.get("bars_received")
        excluded = payload.get("bars_excluded_open")
        if (type(processed) is not int or type(received) is not int or type(excluded) is not int
                or received != len(envelopes) or not 0 <= excluded <= received
                or not 0 <= processed <= received - excluded):
            raise IsolatedWorkerError("bulk result bar counts are invalid")
        if progress.total is not None and (progress.total != received - excluded or progress.done > processed):
            raise IsolatedWorkerError("bulk progress differs from the completed result")
        intent_tape = payload.get("intent_tape")
        if not isinstance(intent_tape, list):
            raise IsolatedWorkerError("bulk result intent tape is invalid")
        if intent_tape:
            from backtest_engine.core.intent_replay import IntentReplayError, require_live_tape
            try:
                require_live_tape(intent_tape)
            except (IntentReplayError, ValueError) as exc:
                raise IsolatedWorkerError("bulk result intent tape failed validation") from exc
            context = self.protocol.execution_context
            fields = ("run_id", "strategy_id", "series_id", "instrument_id", "timeframe")
            if any(any(event.get(key) != context[key] for key in fields)
                   or event.get("stack_id") != context["stack_manifest_hash"] for event in intent_tape):
                raise IsolatedWorkerError("bulk result intent identity mismatch")
        # Execution completion is reported only after the complete result checks.
        progress.report(processed, received - excluded, force=True)
        return {
            "ok": True,
            "bars_processed": processed,
            "bars_received": received,
            "bars_excluded_open": excluded,
            "intent_tape": intent_tape,
            "score_ledger_hash": payload.get("score_ledger_hash"),
            "raw_result": hydrate_bulk_raw_result(payload),
            "result_manifest": manifest,
        }

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is not None:
            return super().__exit__(exc_type, exc, traceback)
        try:
            if self.proc.stdin is not None and not self._closed:
                self.proc.stdin.close()
            try:
                return_code = self.proc.wait(timeout=2)
            except subprocess.TimeoutExpired as error:
                self._kill()
                raise IsolatedWorkerError("bulk worker did not exit after its result") from error
            except OSError as error:
                self._kill()
                raise IsolatedWorkerError("bulk worker exit could not be verified") from error
            if return_code != 0:
                raise IsolatedWorkerError(f"bulk worker exited with status {return_code}")
        finally:
            self._close_pipes()
            self._closed = True
        return None
