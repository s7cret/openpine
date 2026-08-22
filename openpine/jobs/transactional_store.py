"""Transactional SQLite source of truth for ``openpine.job.v1``."""

from __future__ import annotations

import json
import sqlite3
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self, cast

from openpine_contracts import JobState, SchemaValidationError, seal_content_hash, validate_payload
from openpine_contracts.hashing import SERIALIZER_ID

from openpine.build_identity import BuildIdentityError, current_build_identity

SCHEMA_ID = "openpine.job.v1"
JOB_KINDS = (
    "backtest",
    "backfill",
    "compile",
    "optimize",
    "parity",
    "report",
)
_TERMINAL = {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED, JobState.LOST}
_ALLOWED_TRANSITIONS: dict[JobState, frozenset[JobState]] = {
    JobState.QUEUED: frozenset({JobState.RUNNING, JobState.FAILED, JobState.CANCELED}),
    JobState.RUNNING: frozenset(
        {JobState.SUCCEEDED, JobState.FAILED, JobState.CANCELED, JobState.LOST}
    ),
    JobState.FAILED: frozenset({JobState.QUEUED, JobState.CANCELED}),
    JobState.LOST: frozenset({JobState.QUEUED, JobState.CANCELED}),
    JobState.RETRY_WAIT: frozenset({JobState.QUEUED, JobState.CANCELED}),
    JobState.SUCCEEDED: frozenset(),
    JobState.CANCELED: frozenset(),
}


class JobV1Error(ValueError):
    """Typed job.v1 persistence, transition, lease, or identity error."""


class JobV1Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._closed = True
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            isolation_level=None,
            timeout=5.0,
        )
        self._closed = False
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA busy_timeout = 5000")
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = FULL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._initialize_schema()

    def _initialize_schema(self) -> None:
        with self._transaction():
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS jobs (
                    job_id TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    idempotency_key TEXT,
                    state TEXT,
                    version INTEGER NOT NULL DEFAULT 1
                )
                """
            )
            columns = {
                str(row["name"])
                for row in self._conn.execute("PRAGMA table_info(jobs)").fetchall()
            }
            for name, declaration in (
                ("idempotency_key", "TEXT"),
                ("state", "TEXT"),
                ("version", "INTEGER NOT NULL DEFAULT 1"),
            ):
                if name not in columns:
                    self._conn.execute(f"ALTER TABLE jobs ADD COLUMN {name} {declaration}")
            self._conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    seq INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT NOT NULL UNIQUE,
                    job_id TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    FOREIGN KEY(job_id) REFERENCES jobs(job_id)
                )
                """
            )
            self._migrate_existing_jobs_in_transaction()
            try:
                self._conn.execute(
                    """
                    CREATE UNIQUE INDEX IF NOT EXISTS ux_jobs_idempotency_key
                    ON jobs(idempotency_key)
                    WHERE idempotency_key IS NOT NULL
                    """
                )
            except sqlite3.IntegrityError as exc:
                raise JobV1Error("duplicate persisted idempotency keys") from exc
            self._conn.execute(
                "CREATE INDEX IF NOT EXISTS ix_jobs_state ON jobs(state)"
            )

    def _migrate_existing_jobs_in_transaction(self) -> None:
        rows = self._conn.execute(
            "SELECT job_id, payload, version FROM jobs"
        ).fetchall()
        for row in rows:
            try:
                payload = json.loads(row["payload"])
            except (TypeError, json.JSONDecodeError) as exc:
                raise JobV1Error(f"corrupt persisted job: {row['job_id']}") from exc
            changed = False
            if "version" not in payload:
                payload["version"] = max(1, int(row["version"] or 1))
                changed = True
            if changed:
                payload = _rehash(payload)
            self._conn.execute(
                """
                UPDATE jobs
                SET payload = ?, idempotency_key = ?, state = ?, version = ?
                WHERE job_id = ?
                """,
                (
                    _json(payload),
                    payload.get("idempotency_key"),
                    payload.get("state"),
                    int(payload["version"]),
                    row["job_id"],
                ),
            )

    @contextmanager
    def _transaction(self) -> Iterator[None]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self._conn.rollback()
                raise
            else:
                self._conn.commit()

    def close(self) -> None:
        with self._lock:
            if not self._closed:
                self._conn.close()
                self._closed = True

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        self.close()

    def __del__(self) -> None:
        if hasattr(self, "_lock") and hasattr(self, "_closed"):
            self.close()

    def create(
        self,
        *,
        job_id: str | None,
        kind: str,
        actor: str | None = None,
        idempotency_key: str | None = None,
        parent_job_id: str | None = None,
        input_artifact_refs: list[str] | None = None,
        stack_id: str = "openpine-5.0",
        max_retries: int = 3,
    ) -> dict[str, Any]:
        if not job_id:
            raise JobV1Error("job_id required")
        if kind not in JOB_KINDS:
            raise JobV1Error(f"unknown kind: {kind}")
        if max_retries < 0:
            raise JobV1Error("max_retries must be >= 0")
        now = _now_ms()
        with self._transaction():
            if idempotency_key:
                existing = self._by_idempotency_in_transaction(idempotency_key)
                if existing is not None:
                    return existing
            payload = _envelope(
                {
                    "job_id": job_id,
                    "kind": kind,
                    "state": JobState.QUEUED.value,
                    "version": 1,
                    "progress": 0,
                    "started_at_utc_ms": None,
                    "updated_at_utc_ms": now,
                    "finished_at_utc_ms": None,
                    "actor": actor,
                    "input_artifact_refs": list(input_artifact_refs or []),
                    "result_artifact_refs": [],
                    "error_code": None,
                    "idempotency_key": idempotency_key,
                    "lease_owner": None,
                    "lease_deadline_utc_ms": None,
                    "retry_count": 0,
                    "max_retries": max_retries,
                    "parent_job_id": parent_job_id,
                    "child_job_ids": [],
                    "event_cursor": None,
                    "run_id": None,
                },
                created_at_utc_ms=now,
                stack_id=stack_id,
            )
            try:
                self._conn.execute(
                    """
                    INSERT INTO jobs(job_id, payload, idempotency_key, state, version)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        _json(payload),
                        idempotency_key,
                        JobState.QUEUED.value,
                        1,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                if idempotency_key:
                    existing = self._by_idempotency_in_transaction(idempotency_key)
                    if existing is not None:
                        return existing
                raise JobV1Error(f"job already exists: {job_id}") from exc
            if parent_job_id:
                parent = self._get_in_transaction(parent_job_id)
                children = list(parent.get("child_job_ids") or [])
                if job_id not in children:
                    old_version = int(parent["version"])
                    children.append(job_id)
                    parent["child_job_ids"] = children
                    parent["updated_at_utc_ms"] = now
                    parent["version"] = old_version + 1
                    self._store_job_in_transaction(parent, expected_version=old_version)
                    self._append_event_in_transaction(
                        parent_job_id, "child_linked", parent
                    )
            self._append_event_in_transaction(job_id, "created", payload)
            return dict(payload)

    def get(self, job_id: str) -> dict[str, Any]:
        if not job_id:
            raise JobV1Error("job_id required")
        with self._lock:
            return self._get_in_transaction(job_id)

    def _get_in_transaction(self, job_id: str) -> dict[str, Any]:
        row = self._conn.execute(
            "SELECT payload FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise JobV1Error(f"job not found: {job_id}")
        try:
            raw_payload = json.loads(row["payload"])
        except (TypeError, json.JSONDecodeError) as exc:
            raise JobV1Error(f"corrupt persisted job: {job_id}") from exc
        if not isinstance(raw_payload, dict):
            raise JobV1Error(f"corrupt persisted job: {job_id}")
        payload = cast(dict[str, Any], raw_payload)
        try:
            validate_payload(SCHEMA_ID, payload)
        except SchemaValidationError as exc:
            raise JobV1Error(str(exc)) from exc
        return payload

    def list_jobs(
        self,
        *,
        kind: str | None = None,
        state: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
        with self._lock:
            jobs = [
                json.loads(row["payload"])
                for row in self._conn.execute("SELECT payload FROM jobs")
            ]
        jobs.sort(
            key=lambda item: (-int(item["created_at_utc_ms"]), str(item["job_id"]))
        )
        if kind:
            jobs = [item for item in jobs if item["kind"] == kind]
        if state:
            jobs = [item for item in jobs if item["state"] == state]
        if cursor:
            for index, item in enumerate(jobs):
                if item["job_id"] == cursor:
                    jobs = jobs[index + 1 :]
                    break
        bounded_limit = max(1, min(limit, 1_000))
        page = jobs[:bounded_limit]
        next_cursor = page[-1]["job_id"] if len(jobs) > len(page) else None
        return {"items": page, "cursor": next_cursor}

    def mark_running(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_deadline_utc_ms: int,
        now_ms: int | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        now = _now_ms() if now_ms is None else now_ms
        if not lease_owner:
            raise JobV1Error("lease owner required")
        if lease_deadline_utc_ms <= now:
            raise JobV1Error("lease deadline must be in the future")
        return self._transition(
            job_id,
            target=JobState.RUNNING,
            event_type="running",
            expected_version=expected_version,
            now_ms=now,
            updates={
                "started_at_utc_ms": now,
                "lease_owner": lease_owner,
                "lease_deadline_utc_ms": lease_deadline_utc_ms,
            },
        )

    def renew_lease(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_deadline_utc_ms: int,
        now_ms: int | None = None,
        expected_version: int | None = None,
    ) -> dict[str, Any]:
        now = _now_ms() if now_ms is None else now_ms
        if lease_deadline_utc_ms <= now:
            raise JobV1Error("lease deadline must be in the future")
        with self._transaction():
            job = self._get_in_transaction(job_id)
            self._require_version(job, expected_version)
            self._require_live_lease(job, lease_owner=lease_owner, now_ms=now)
            old_version = int(job["version"])
            job["lease_deadline_utc_ms"] = lease_deadline_utc_ms
            job["updated_at_utc_ms"] = now
            job["version"] = old_version + 1
            self._store_job_in_transaction(job, expected_version=old_version)
            self._append_event_in_transaction(job_id, "lease_renewed", job)
            return dict(job)

    def mark_succeeded(
        self,
        job_id: str,
        *,
        lease_owner: str,
        expected_version: int | None = None,
        now_ms: int | None = None,
        result_artifact_refs: list[str] | None = None,
    ) -> dict[str, Any]:
        now = _now_ms() if now_ms is None else now_ms
        with self._transaction():
            job = self._get_in_transaction(job_id)
            self._require_version(job, expected_version)
            if JobState(job["state"]) is not JobState.RUNNING:
                raise JobV1Error(
                    f"transition {job['state']} -> {JobState.SUCCEEDED.value} is not allowed"
                )
            self._require_live_lease(job, lease_owner=lease_owner, now_ms=now)
            return self._transition_in_transaction(
                job,
                target=JobState.SUCCEEDED,
                event_type="succeeded",
                now_ms=now,
                updates={
                    "progress": 100,
                    "finished_at_utc_ms": now,
                    "result_artifact_refs": list(result_artifact_refs or []),
                    "lease_owner": None,
                    "lease_deadline_utc_ms": None,
                },
            )

    def mark_failed(
        self,
        job_id: str,
        *,
        error_code: str,
        lease_owner: str | None = None,
        expected_version: int | None = None,
        now_ms: int | None = None,
    ) -> dict[str, Any]:
        now = _now_ms() if now_ms is None else now_ms
        with self._transaction():
            job = self._get_in_transaction(job_id)
            self._require_version(job, expected_version)
            state = JobState(job["state"])
            if state is JobState.RUNNING:
                self._require_live_lease(job, lease_owner=lease_owner, now_ms=now)
            return self._transition_in_transaction(
                job,
                target=JobState.FAILED,
                event_type="failed",
                now_ms=now,
                updates={
                    "finished_at_utc_ms": now,
                    "error_code": error_code,
                    "lease_owner": None,
                    "lease_deadline_utc_ms": None,
                },
            )

    def cancel(
        self, job_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        with self._transaction():
            job = self._get_in_transaction(job_id)
            if JobState(job["state"]) is JobState.CANCELED:
                return job
            return self._transition_in_transaction(
                job,
                target=JobState.CANCELED,
                event_type="canceled",
                now_ms=_now_ms(),
                updates={
                    "finished_at_utc_ms": _now_ms(),
                    "lease_owner": None,
                    "lease_deadline_utc_ms": None,
                },
                event_id=idempotency_key,
            )

    def retry(self, job_id: str) -> dict[str, Any]:
        with self._transaction():
            job = self._get_in_transaction(job_id)
            if JobState(job["state"]) is JobState.QUEUED and int(job["retry_count"]) > 0:
                return job
            next_count = int(job["retry_count"]) + 1
            max_retries = job.get("max_retries")
            if max_retries is not None and next_count > int(max_retries):
                raise JobV1Error("retry limit reached")
            return self._transition_in_transaction(
                job,
                target=JobState.QUEUED,
                event_type="retried",
                now_ms=_now_ms(),
                updates={
                    "retry_count": next_count,
                    "finished_at_utc_ms": None,
                    "error_code": None,
                },
            )

    def recover_lost_leases(self, *, now_ms: int) -> int:
        recovered = 0
        with self._transaction():
            rows = self._conn.execute(
                "SELECT payload FROM jobs WHERE state = ? ORDER BY job_id",
                (JobState.RUNNING.value,),
            ).fetchall()
            for row in rows:
                job = json.loads(row["payload"])
                deadline = job.get("lease_deadline_utc_ms")
                if deadline is None or int(deadline) >= now_ms:
                    continue
                self._transition_in_transaction(
                    job,
                    target=JobState.LOST,
                    event_type="lost",
                    now_ms=now_ms,
                    updates={
                        "finished_at_utc_ms": now_ms,
                        "lease_owner": None,
                        "lease_deadline_utc_ms": None,
                    },
                )
                recovered += 1
        return recovered

    def events(self, *, after: str = "") -> list[dict[str, Any]]:
        with self._lock:
            if after:
                anchor = self._conn.execute(
                    "SELECT seq FROM events WHERE event_id = ?", (after,)
                ).fetchone()
                if anchor is None:
                    return []
                rows = self._conn.execute(
                    "SELECT payload FROM events WHERE seq > ? ORDER BY seq ASC",
                    (int(anchor["seq"]),),
                ).fetchall()
            else:
                rows = self._conn.execute(
                    "SELECT payload FROM events ORDER BY seq ASC"
                ).fetchall()
        return [json.loads(row["payload"]) for row in rows]

    def needs_resync(self, *, after: str) -> bool:
        if not after:
            return False
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM events WHERE event_id = ?", (after,)
            ).fetchone()
        return row is None

    def _transition(
        self,
        job_id: str,
        *,
        target: JobState,
        event_type: str,
        expected_version: int | None,
        now_ms: int,
        updates: dict[str, Any],
    ) -> dict[str, Any]:
        with self._transaction():
            job = self._get_in_transaction(job_id)
            self._require_version(job, expected_version)
            return self._transition_in_transaction(
                job,
                target=target,
                event_type=event_type,
                now_ms=now_ms,
                updates=updates,
            )

    def _transition_in_transaction(
        self,
        job: dict[str, Any],
        *,
        target: JobState,
        event_type: str,
        now_ms: int,
        updates: dict[str, Any],
        event_id: str | None = None,
    ) -> dict[str, Any]:
        current = JobState(job["state"])
        if target not in _ALLOWED_TRANSITIONS[current]:
            raise JobV1Error(
                f"transition {current.value} -> {target.value} is not allowed"
            )
        old_version = int(job["version"])
        job.update(updates)
        job["state"] = target.value
        job["updated_at_utc_ms"] = now_ms
        job["version"] = old_version + 1
        self._store_job_in_transaction(job, expected_version=old_version)
        self._append_event_in_transaction(
            str(job["job_id"]), event_type, job, event_id=event_id
        )
        return dict(job)

    @staticmethod
    def _require_version(job: dict[str, Any], expected_version: int | None) -> None:
        if expected_version is not None and int(job["version"]) != expected_version:
            raise JobV1Error(
                f"version conflict: expected {expected_version}, got {job['version']}"
            )

    @staticmethod
    def _require_live_lease(
        job: dict[str, Any], *, lease_owner: str | None, now_ms: int
    ) -> None:
        if not lease_owner or job.get("lease_owner") != lease_owner:
            raise JobV1Error("lease owner mismatch")
        deadline = job.get("lease_deadline_utc_ms")
        if deadline is None or int(deadline) < now_ms:
            raise JobV1Error("lease expired")

    def _store_job_in_transaction(
        self, payload: dict[str, Any], *, expected_version: int
    ) -> None:
        sealed = _rehash(payload)
        payload.clear()
        payload.update(sealed)
        cursor = self._conn.execute(
            """
            UPDATE jobs
            SET payload = ?, idempotency_key = ?, state = ?, version = ?
            WHERE job_id = ? AND version = ?
            """,
            (
                _json(payload),
                payload.get("idempotency_key"),
                payload["state"],
                int(payload["version"]),
                payload["job_id"],
                expected_version,
            ),
        )
        if cursor.rowcount != 1:
            raise JobV1Error("version conflict while storing job")

    def _append_event_in_transaction(
        self,
        job_id: str,
        event_type: str,
        job: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        stable_event_id = event_id or f"{job_id}:{event_type}:v{int(job['version'])}"
        existing = self._conn.execute(
            "SELECT seq FROM events WHERE event_id = ?", (stable_event_id,)
        ).fetchone()
        if existing is not None:
            job["event_cursor"] = stable_event_id
            return
        cursor = self._conn.execute(
            "INSERT INTO events(event_id, job_id, payload) VALUES (?, ?, ?)",
            (stable_event_id, job_id, "{}"),
        )
        if cursor.lastrowid is None:
            raise JobV1Error("event sequence was not allocated")
        sequence = int(cursor.lastrowid)
        event = {
            "event_id": stable_event_id,
            "sequence": sequence,
            "type": event_type,
            "job_id": job_id,
            "state": job["state"],
            "version": int(job["version"]),
        }
        self._conn.execute(
            "UPDATE events SET payload = ? WHERE seq = ?",
            (_json(event), sequence),
        )
        job["event_cursor"] = stable_event_id
        sealed = _rehash(job)
        job.clear()
        job.update(sealed)
        self._conn.execute(
            "UPDATE jobs SET payload = ? WHERE job_id = ? AND version = ?",
            (_json(job), job_id, int(job["version"])),
        )

    def _by_idempotency_in_transaction(self, key: str) -> dict[str, Any] | None:
        row = self._conn.execute(
            "SELECT payload FROM jobs WHERE idempotency_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        payload = json.loads(row["payload"])
        if not isinstance(payload, dict):
            raise JobV1Error("corrupt persisted idempotency row")
        return cast(dict[str, Any], payload)


def _now_ms() -> int:
    return int(time.time() * 1000)


def _envelope(
    body: dict[str, Any], *, created_at_utc_ms: int, stack_id: str
) -> dict[str, Any]:
    try:
        identity = current_build_identity()
    except BuildIdentityError as exc:
        raise JobV1Error(str(exc)) from exc
    payload: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": "1.0.0",
        "producer": "openpine",
        "producer_version": identity.contract_version,
        "producer_commit": identity.commit,
        "stack_id": stack_id,
        "created_at_utc_ms": created_at_utc_ms,
        "serializer_id": SERIALIZER_ID,
        "content_hash_alg": "sha256",
    }
    payload.update(body)
    return _rehash(payload)


def _rehash(payload: dict[str, Any]) -> dict[str, Any]:
    try:
        sealed = seal_content_hash(payload, schema_id=SCHEMA_ID)
        validate_payload(SCHEMA_ID, sealed)
    except SchemaValidationError as exc:
        raise JobV1Error(str(exc)) from exc
    return cast(dict[str, Any], sealed)


def _json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)
