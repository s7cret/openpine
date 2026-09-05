"""BULK_BACKTEST parent session: stream bars once, no per-bar IPC."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from types import SimpleNamespace
from typing import Any

from openpine.runtime.isolated_worker import InteractiveWorkerSession, IsolatedWorkerError

BULK_MESSAGE_LIMIT_BYTES = 900_000


def chunk_bulk_frames(
    bars: Sequence[Mapping[str, Any]],
    *,
    max_bytes: int = BULK_MESSAGE_LIMIT_BYTES,
) -> list[dict[str, Any]]:
    """Split sealed bar envelopes into stdin frames under the worker line cap."""

    if max_bytes < 64:
        raise ValueError("bulk bar exceeds message limit")
    frames: list[dict[str, Any]] = []
    current: list[Mapping[str, Any]] = []
    current_size = 0
    overhead = len('{"kind":"BULK_BARS","bars":[],"last":false}')
    for bar in bars:
        encoded = json.dumps(bar, separators=(",", ":")).encode("utf-8")
        piece = len(encoded) + 1
        if piece + overhead > max_bytes:
            raise ValueError("bulk bar exceeds message limit")
        if current and current_size + piece + overhead > max_bytes:
            frames.append({"kind": "BULK_BARS", "bars": current, "last": False})
            current = []
            current_size = 0
        current.append(bar)
        current_size += piece
    frames.append({"kind": "BULK_BARS", "bars": current, "last": True})
    return frames


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
    ) -> dict[str, Any]:
        del engine_config
        if not envelopes:
            raise IsolatedWorkerError("bulk backtest requires canonical bar envelopes")
        frames = chunk_bulk_frames(envelopes)
        for frame in frames:
            self._write_json_line(frame)
        previous_timeout = self.timeout_s
        self.timeout_s = self.bulk_idle_timeout_s
        try:
            while True:
                payload = self._read_message(require_protocol=False)
                kind = payload.get("kind")
                if kind == "BULK_PROGRESS":
                    continue
                if kind == "BULK_RESULT":
                    break
                raise IsolatedWorkerError(
                    f"bulk worker returned unsupported message: {kind!r}"
                )
        finally:
            self.timeout_s = previous_timeout
        intent_tape = payload.get("intent_tape")
        if not isinstance(intent_tape, list):
            raise IsolatedWorkerError("bulk result intent tape is invalid")
        return {
            "ok": True,
            "bars_processed": int(payload.get("bars_processed") or 0),
            "intent_tape": intent_tape,
            "score_ledger_hash": payload.get("score_ledger_hash"),
            "raw_result": hydrate_bulk_raw_result(payload),
        }

    def __exit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            try:
                if self.proc.stdin is not None and not self._closed:
                    self.proc.stdin.close()
                try:
                    self.proc.wait(timeout=2)
                except Exception:
                    self._kill()
            finally:
                self._close_pipes()
                self._closed = True
            return None
        return super().__exit__(exc_type, exc, traceback)
