"""StateStore — strategy state persistence. Sections 7.8, 17, 30.6, 33.7."""

from __future__ import annotations

import hashlib
import json
import os
import pickle
import tempfile
import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

import msgpack

from openpine.state.errors import (
    InvalidSnapshotError,
    SnapshotNotFoundError,
)


class SavePolicy(StrEnum):
    """Snapshot save policy — section 33.7."""
    EVERY_BAR = "every_bar"
    ON_REQUEST = "on_request"
    INTERVAL = "interval"


@dataclass
class SnapshotMetadata:
    """Section 17.1: snapshot metadata stored in SQLite."""
    snapshot_id: str
    strategy_id: str
    artifact_id: str
    params_hash: str
    instrument_key: dict  # serialized
    timeframe: dict  # serialized
    bar_time: int  # bar timestamp of snapshot
    saved_at: int  # ms
    size_bytes: int
    status: str = "active"  # active / superseded / invalid
    reason: str = "scheduled"  # scheduled / position_change / order_event / signal / manual / rebuild
    data_fingerprint: str | None = None
    state_encoding: str = "msgpack"

    def to_dict(self) -> dict[str, Any]:
        return {
            "snapshot_id": self.snapshot_id,
            "strategy_id": self.strategy_id,
            "artifact_id": self.artifact_id,
            "params_hash": self.params_hash,
            "instrument_key": self.instrument_key,
            "timeframe": self.timeframe,
            "bar_time": self.bar_time,
            "saved_at": self.saved_at,
            "size_bytes": self.size_bytes,
            "status": self.status,
            "reason": self.reason,
            "data_fingerprint": self.data_fingerprint,
            "state_encoding": self.state_encoding,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> SnapshotMetadata:
        return cls(
            snapshot_id=d["snapshot_id"],
            strategy_id=d["strategy_id"],
            artifact_id=d["artifact_id"],
            params_hash=d["params_hash"],
            instrument_key=d["instrument_key"],
            timeframe=d["timeframe"],
            bar_time=d["bar_time"],
            saved_at=d["saved_at"],
            size_bytes=d["size_bytes"],
            status=d.get("status", "active"),
            reason=d.get("reason", "scheduled"),
            data_fingerprint=d.get("data_fingerprint"),
            state_encoding=d.get("state_encoding", "msgpack"),
        )


@dataclass
class StrategyState:
    """Section 5.10: strategy state snapshot."""
    strategy_id: str
    artifact_id: str
    params_hash: str
    instrument_key: dict  # serialized instrument key
    timeframe: dict  # serialized timeframe
    state_data: dict  # serialized strategy variables
    bar_time: int  # last processed bar timestamp
    saved_at: int  # ms timestamp

    def to_payload(self) -> dict[str, Any]:
        return {
            "schema_version": "openpine.state.v1",
            "state_key": {
                "strategy_id": self.strategy_id,
                "artifact_id": self.artifact_id,
                "params_hash": self.params_hash,
                "instrument_key": self.instrument_key,
                "timeframe": self.timeframe,
            },
            "last_processed_bar_time": self.bar_time,
            "runtime_state": self.state_data,
            "strategy_state": {},
            "orders_state": {},
        }

    def checksum(self) -> str:
        payload = self.to_payload()
        packed = msgpack.packb(payload, use_bin_type=True)
        return hashlib.sha256(packed).hexdigest()

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> StrategyState:
        state_key = payload["state_key"]
        return cls(
            strategy_id=state_key["strategy_id"],
            artifact_id=state_key["artifact_id"],
            params_hash=state_key["params_hash"],
            instrument_key=state_key["instrument_key"],
            timeframe=state_key["timeframe"],
            state_data=payload.get("runtime_state", {}),
            bar_time=payload.get("last_processed_bar_time", 0),
            saved_at=0,
        )


class StateStore:
    """Section 7.8: strategy state persistence.

    Snapshot write is atomic: temp file → validate → rename → metadata insert.
    Section 33.7: save_policy defaults to every_bar.
    Section 33.2: failed process_next_bar creates NO snapshot.
    """

    def __init__(
        self,
        storage_dir: Path,
        save_policy: SavePolicy = SavePolicy.EVERY_BAR,
        save_interval_bars: int = 1,
    ) -> None:
        self.storage_dir = Path(storage_dir)
        self.save_policy = save_policy
        self.save_interval_bars = save_interval_bars
        self._bars_since_last_save: dict[str, int] = defaultdict(int)

        # In-memory snapshot registry: strategy_id -> list of SnapshotMetadata
        self._snapshots: dict[str, list[SnapshotMetadata]] = defaultdict(list)

        self.storage_dir.mkdir(parents=True, exist_ok=True)
        self._load_metadata_index()

    def _state_dir(self, strategy_id: str) -> Path:
        return self.storage_dir / f"strategy_id={strategy_id}"

    def _snapshot_path(self, strategy_id: str, snapshot_id: str) -> Path:
        return self._state_dir(strategy_id) / f"snap_{snapshot_id}.state.msgpack.zst"

    def _debug_path(self, strategy_id: str, snapshot_id: str) -> Path:
        return self._state_dir(strategy_id) / f"snap_{snapshot_id}.debug.json"

    def _metadata_index_path(self) -> Path:
        return self.storage_dir / "snapshots.index.json"

    @staticmethod
    def _snapshot_payload(state: StrategyState, *, data_fingerprint: str | None = None) -> dict[str, Any]:
        payload = state.to_payload()
        if data_fingerprint is not None:
            payload["data_fingerprint"] = data_fingerprint
        return payload

    @staticmethod
    def _encode_payload(payload: dict[str, Any]) -> tuple[bytes, str, str]:
        try:
            packed = msgpack.packb(payload, use_bin_type=True)
            return packed, "msgpack", hashlib.sha256(packed).hexdigest()
        except (TypeError, ValueError):
            packed = pickle.dumps(payload, protocol=pickle.HIGHEST_PROTOCOL)
            return packed, "pickle", hashlib.sha256(packed).hexdigest()

    @staticmethod
    def _decode_payload(packed: bytes, encoding: str) -> dict[str, Any]:
        if encoding == "pickle":
            if os.environ.get("OPENPINE_ALLOW_PICKLE_STATE") != "1":
                raise InvalidSnapshotError(
                    "pickle snapshot loading is disabled; set "
                    "OPENPINE_ALLOW_PICKLE_STATE=1 only for trusted local snapshots"
                )
            return pickle.loads(packed)
        return msgpack.unpackb(packed, raw=False)

    @staticmethod
    def _debug_payload(payload: dict[str, Any]) -> dict[str, Any]:
        return {
            **payload,
            "runtime_state": "<redacted>",
            "strategy_state": "<redacted>",
            "orders_state": "<redacted>",
        }

    def save_snapshot(
        self,
        state: StrategyState,
        reason: str = "scheduled",
        failed_bar: bool = False,
        data_fingerprint: str | None = None,
    ) -> SnapshotMetadata | None:
        """Save state snapshot atomically.

        Section 33.2: returns None if failed_bar is True.
        Respects save_policy: if every_bar, always save.
        If interval, saves every N bars (tracked per strategy).
        """
        # CRITICAL v3: no snapshot on failed bar
        if failed_bar:
            return None

        bars_since = self._bars_since_last_save[state.strategy_id]

        # Check interval policy
        if self.save_policy == SavePolicy.INTERVAL:
            if bars_since < self.save_interval_bars:
                self._bars_since_last_save[state.strategy_id] += 1
                return None
        elif self.save_policy == SavePolicy.ON_REQUEST:
            # ON_REQUEST only saves on explicit call (not tracked in bars_since)
            pass

        snapshot_id = str(uuid.uuid4())
        bar_time = state.bar_time
        saved_at = int(datetime.now().timestamp() * 1000)

        payload = self._snapshot_payload(state, data_fingerprint=data_fingerprint)
        packed, encoding, checksum = self._encode_payload(payload)
        payload["checksum"] = checksum
        payload["state_encoding"] = encoding

        # Atomic write: temp file → validate → rename
        sd = self._state_dir(state.strategy_id)
        sd.mkdir(parents=True, exist_ok=True)

        snap_path = self._snapshot_path(state.strategy_id, snapshot_id)
        tmp_fd, tmp_path = tempfile.mkstemp(dir=sd, suffix=".state.tmp")
        try:
            with os.fdopen(tmp_fd, "wb") as f:
                f.write(packed)
                f.flush()
                os.fsync(f.fileno())
            # Rename to final name
            Path(tmp_path).replace(snap_path)
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise

        size_bytes = snap_path.stat().st_size

        # Write debug JSON
        debug_path = self._debug_path(state.strategy_id, snapshot_id)
        debug_path.write_text(json.dumps(self._debug_payload(payload), indent=2))

        # Build metadata
        meta = SnapshotMetadata(
            snapshot_id=snapshot_id,
            strategy_id=state.strategy_id,
            artifact_id=state.artifact_id,
            params_hash=state.params_hash,
            instrument_key=state.instrument_key,
            timeframe=state.timeframe,
            bar_time=bar_time,
            saved_at=saved_at,
            size_bytes=size_bytes,
            reason=reason,
            data_fingerprint=data_fingerprint,
            state_encoding=encoding,
        )

        # Mark previous active snapshots as superseded
        for prev in self._snapshots[state.strategy_id]:
            if prev.status == "active":
                prev.status = "superseded"

        self._snapshots[state.strategy_id].append(meta)
        self._bars_since_last_save[state.strategy_id] = 0
        self._persist_metadata_index()

        return meta

    def load_snapshot(self, strategy_id: str) -> StrategyState | None:
        """Load most recent ACTIVE compatible snapshot for strategy."""
        meta = self.latest_snapshot_metadata(strategy_id)
        return self._load_state(meta) if meta is not None else None

    def latest_snapshot_metadata(
        self,
        strategy_id: str,
        *,
        artifact_id: str | None = None,
        params_hash: str | None = None,
        instrument_key: dict | None = None,
        timeframe: dict | None = None,
        data_fingerprint: str | None = None,
        at_or_before_bar_time: int | None = None,
    ) -> SnapshotMetadata | None:
        """Return newest active snapshot compatible with the supplied key."""

        snapshots = self._snapshots.get(strategy_id, [])
        active = [
            s for s in snapshots
            if s.status == "active"
            and (artifact_id is None or s.artifact_id == artifact_id)
            and (params_hash is None or s.params_hash == params_hash)
            and (instrument_key is None or s.instrument_key == instrument_key)
            and (timeframe is None or s.timeframe == timeframe)
            and (data_fingerprint is None or s.data_fingerprint == data_fingerprint)
            and (at_or_before_bar_time is None or s.bar_time <= at_or_before_bar_time)
        ]
        if not active:
            return None
        return max(active, key=lambda s: (s.bar_time, s.saved_at))

    def load_latest_compatible(self, strategy_id: str, **filters: Any) -> StrategyState | None:
        """Load newest active snapshot matching artifact/params/instrument/timeframe/fingerprint."""

        meta = self.latest_snapshot_metadata(strategy_id, **filters)
        return self._load_state(meta) if meta is not None else None

    def save_runtime_snapshot(
        self,
        *,
        strategy_id: str,
        artifact_id: str,
        params_hash: str,
        instrument_key: dict,
        timeframe: dict,
        runtime_state: object,
        bar_time: int,
        reason: str = "scheduled",
        data_fingerprint: str | None = None,
        failed_bar: bool = False,
    ) -> SnapshotMetadata | None:
        """Persist an arbitrary serializable runtime/resume state under the strategy key."""

        state = StrategyState(
            strategy_id=strategy_id,
            artifact_id=artifact_id,
            params_hash=params_hash,
            instrument_key=instrument_key,
            timeframe=timeframe,
            state_data=runtime_state,
            bar_time=bar_time,
            saved_at=int(datetime.now().timestamp() * 1000),
        )
        return self.save_snapshot(
            state,
            reason=reason,
            failed_bar=failed_bar,
            data_fingerprint=data_fingerprint,
        )

    def load_runtime_snapshot(self, strategy_id: str, **filters: Any) -> object | None:
        """Load the runtime_state payload from the newest compatible snapshot."""

        state = self.load_latest_compatible(strategy_id, **filters)
        return state.state_data if state is not None else None

    def _load_state(self, meta: SnapshotMetadata) -> StrategyState | None:
        snap_path = self._snapshot_path(meta.strategy_id, meta.snapshot_id)
        if not snap_path.exists():
            return None
        packed = snap_path.read_bytes()
        payload = self._decode_payload(packed, meta.state_encoding)
        # Verify checksum
        stored_checksum = payload.pop("checksum", None)
        payload.pop("state_encoding", None)
        computed = hashlib.sha256(packed).hexdigest()
        if stored_checksum and stored_checksum != computed:
            raise InvalidSnapshotError(
                f"Checksum mismatch for snapshot {meta.snapshot_id}"
            )
        return StrategyState.from_payload(payload)

    def list_snapshots(self, strategy_id: str) -> list[SnapshotMetadata]:
        """List all snapshots for strategy, newest first."""
        return sorted(
            self._snapshots.get(strategy_id, []),
            key=lambda s: s.saved_at,
            reverse=True,
        )

    def delete_snapshot(self, snapshot_id: str) -> None:
        """Delete specific snapshot and its files."""
        for strategy_id, metas in self._snapshots.items():
            for i, meta in enumerate(metas):
                if meta.snapshot_id == snapshot_id:
                    snap_path = self._snapshot_path(strategy_id, snapshot_id)
                    snap_path.unlink(missing_ok=True)
                    debug_path = self._debug_path(strategy_id, snapshot_id)
                    debug_path.unlink(missing_ok=True)
                    metas.pop(i)
                    self._persist_metadata_index()
                    return
        raise SnapshotNotFoundError(f"Snapshot not found: {snapshot_id}")

    def get_save_policy(self) -> tuple[SavePolicy, int]:
        """Return current save_policy and interval."""
        return self.save_policy, self.save_interval_bars

    def set_save_policy(self, policy: SavePolicy, interval_bars: int = 1) -> None:
        """Update save policy (section 33.7)."""
        self.save_policy = policy
        self.save_interval_bars = max(1, interval_bars)

    def mark_invalid(self, strategy_id: str, since_bar_time: int | None = None) -> None:
        """Mark snapshots as potentially invalid after data repair (section 30.8)."""
        for meta in self._snapshots.get(strategy_id, []):
            if since_bar_time is None or meta.bar_time >= since_bar_time:
                meta.status = "invalid"
        self._persist_metadata_index()

    def _load_metadata_index(self) -> None:
        path = self._metadata_index_path()
        if not path.exists():
            return
        try:
            raw = json.loads(path.read_text())
        except Exception:
            return
        for item in raw.get("snapshots", []):
            meta = SnapshotMetadata.from_dict(item)
            if self._snapshot_path(meta.strategy_id, meta.snapshot_id).exists():
                self._snapshots[meta.strategy_id].append(meta)

    def _persist_metadata_index(self) -> None:
        payload = {
            "schema_version": "openpine.state.index.v1",
            "snapshots": [
                meta.to_dict()
                for metas in self._snapshots.values()
                for meta in metas
            ],
        }
        tmp_fd, tmp_path = tempfile.mkstemp(dir=self.storage_dir, suffix=".index.tmp")
        try:
            with os.fdopen(tmp_fd, "w", encoding="utf-8") as f:
                json.dump(payload, f, indent=2, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            Path(tmp_path).replace(self._metadata_index_path())
        except Exception:
            Path(tmp_path).unlink(missing_ok=True)
            raise
