"""Persisted openpine.job.v1 store. In-memory JobScheduler is not the source of truth."""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from openpine_contracts import JobState, SchemaValidationError, validate_payload
from openpine_contracts.hashing import SERIALIZER_ID, content_hash

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


class JobV1Error(ValueError):
    """Typed job.v1 persistence / identity error."""


class JobV1Store:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute(
            "CREATE TABLE IF NOT EXISTS jobs (job_id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
        )
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS events (
                seq INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id TEXT NOT NULL UNIQUE,
                job_id TEXT NOT NULL,
                payload TEXT NOT NULL
            )
            """)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

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
        if idempotency_key:
            existing = self._by_idempotency(idempotency_key)
            if existing is not None:
                return existing
        now = _now_ms()
        payload = _envelope(
            {
                "job_id": job_id,
                "kind": kind,
                "state": JobState.QUEUED.value,
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
        self._put(payload)
        if parent_job_id:
            parent = self.get(parent_job_id)
            children = list(parent.get("child_job_ids") or [])
            if job_id not in children:
                children.append(job_id)
                parent["child_job_ids"] = children
                self._put(_rehash(parent))
        self._append_event(job_id, "created", payload)
        return self.get(job_id)

    def get(self, job_id: str) -> dict[str, Any]:
        if not job_id:
            raise JobV1Error("job_id required")
        row = self._conn.execute(
            "SELECT payload FROM jobs WHERE job_id = ?", (job_id,)
        ).fetchone()
        if row is None:
            raise JobV1Error(f"job not found: {job_id}")
        payload = json.loads(row["payload"])
        validate_payload(SCHEMA_ID, payload)
        return payload

    def list_jobs(
        self,
        *,
        kind: str | None = None,
        state: str | None = None,
        cursor: str | None = None,
        limit: int = 50,
    ) -> dict[str, Any]:
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
            start = 0
            for index, item in enumerate(jobs):
                if item["job_id"] == cursor:
                    start = index + 1
                    break
            jobs = jobs[start:]
        page = jobs[: max(1, min(limit, 200))]
        next_cursor = page[-1]["job_id"] if len(jobs) > len(page) else None
        return {"items": page, "cursor": next_cursor}

    def mark_running(
        self,
        job_id: str,
        *,
        lease_owner: str,
        lease_deadline_utc_ms: int,
    ) -> dict[str, Any]:
        job = self.get(job_id)
        now = _now_ms()
        job["state"] = JobState.RUNNING.value
        job["started_at_utc_ms"] = job["started_at_utc_ms"] or now
        job["updated_at_utc_ms"] = now
        job["lease_owner"] = lease_owner
        job["lease_deadline_utc_ms"] = lease_deadline_utc_ms
        self._put(_rehash(job))
        self._append_event(job_id, "running", job)
        return self.get(job_id)

    def mark_succeeded(
        self, job_id: str, *, result_artifact_refs: list[str] | None = None
    ) -> dict[str, Any]:
        job = self.get(job_id)
        now = _now_ms()
        job["state"] = JobState.SUCCEEDED.value
        job["progress"] = 100
        job["updated_at_utc_ms"] = now
        job["finished_at_utc_ms"] = now
        job["result_artifact_refs"] = list(result_artifact_refs or [])
        job["lease_owner"] = None
        job["lease_deadline_utc_ms"] = None
        self._put(_rehash(job))
        self._append_event(job_id, "succeeded", job)
        return self.get(job_id)

    def mark_failed(self, job_id: str, *, error_code: str) -> dict[str, Any]:
        job = self.get(job_id)
        now = _now_ms()
        job["state"] = JobState.FAILED.value
        job["updated_at_utc_ms"] = now
        job["finished_at_utc_ms"] = now
        job["error_code"] = error_code
        job["lease_owner"] = None
        job["lease_deadline_utc_ms"] = None
        self._put(_rehash(job))
        self._append_event(job_id, "failed", job)
        return self.get(job_id)

    def cancel(
        self, job_id: str, *, idempotency_key: str | None = None
    ) -> dict[str, Any]:
        job = self.get(job_id)
        if job["state"] == JobState.CANCELED.value:
            return job
        if job["state"] in {JobState.SUCCEEDED.value, JobState.LOST.value}:
            raise JobV1Error(f"cannot cancel {job['state']}")
        now = _now_ms()
        job["state"] = JobState.CANCELED.value
        job["updated_at_utc_ms"] = now
        job["finished_at_utc_ms"] = now
        job["lease_owner"] = None
        job["lease_deadline_utc_ms"] = None
        self._put(_rehash(job))
        self._append_event(job_id, "canceled", job, event_id=idempotency_key)
        return self.get(job_id)

    def retry(self, job_id: str) -> dict[str, Any]:
        job = self.get(job_id)
        if job["state"] == JobState.QUEUED.value and int(job["retry_count"]) > 0:
            return job
        if job["state"] not in {
            JobState.FAILED.value,
            JobState.LOST.value,
            JobState.RETRY_WAIT.value,
        }:
            raise JobV1Error(f"cannot retry {job['state']}")
        max_retries = job.get("max_retries")
        next_count = int(job["retry_count"]) + 1
        if max_retries is not None and next_count > int(max_retries):
            raise JobV1Error("retry limit reached")
        now = _now_ms()
        job["state"] = JobState.QUEUED.value
        job["retry_count"] = next_count
        job["updated_at_utc_ms"] = now
        job["finished_at_utc_ms"] = None
        job["error_code"] = None
        self._put(_rehash(job))
        self._append_event(job_id, "retried", job)
        return self.get(job_id)

    def recover_lost_leases(self, *, now_ms: int) -> int:
        recovered = 0
        for item in self.list_jobs(state=JobState.RUNNING.value, limit=200)["items"]:
            deadline = item.get("lease_deadline_utc_ms")
            if deadline is not None and int(deadline) < now_ms:
                item["state"] = JobState.LOST.value
                item["updated_at_utc_ms"] = now_ms
                item["finished_at_utc_ms"] = now_ms
                item["lease_owner"] = None
                self._put(_rehash(item))
                self._append_event(str(item["job_id"]), "lost", item)
                recovered += 1
        return recovered

    def events(self, *, after: str = "") -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT event_id, seq, payload FROM events ORDER BY seq ASC"
        ).fetchall()
        items = []
        skip = bool(after)
        for row in rows:
            if skip:
                if row["event_id"] == after:
                    skip = False
                continue
            items.append(json.loads(row["payload"]))
        return items

    def needs_resync(self, *, after: str) -> bool:
        if not after:
            return False
        row = self._conn.execute(
            "SELECT 1 FROM events WHERE event_id = ?", (after,)
        ).fetchone()
        return row is None

    def _by_idempotency(self, key: str) -> dict[str, Any] | None:
        for row in self._conn.execute("SELECT payload FROM jobs"):
            payload = json.loads(row["payload"])
            if payload.get("idempotency_key") == key:
                return payload
        return None

    def _put(self, payload: dict[str, Any]) -> None:
        validate_payload(SCHEMA_ID, payload)
        self._conn.execute(
            "INSERT OR REPLACE INTO jobs(job_id, payload) VALUES (?, ?)",
            (payload["job_id"], json.dumps(payload, separators=(",", ":"))),
        )
        self._conn.commit()

    def _append_event(
        self,
        job_id: str,
        event_type: str,
        job: dict[str, Any],
        *,
        event_id: str | None = None,
    ) -> None:
        if event_id:
            existing = self._conn.execute(
                "SELECT payload FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            if existing is not None:
                return
        else:
            event_id = f"{job_id}:{event_type}:{int(job['updated_at_utc_ms'])}"
        event = {
            "event_id": event_id,
            "type": event_type,
            "job_id": job_id,
            "state": job["state"],
        }
        self._conn.execute(
            "INSERT INTO events(event_id, job_id, payload) VALUES (?, ?, ?)",
            (event_id, job_id, json.dumps(event, separators=(",", ":"))),
        )
        job["event_cursor"] = event_id
        self._conn.execute(
            "UPDATE jobs SET payload = ? WHERE job_id = ?",
            (json.dumps(_rehash(job), separators=(",", ":")), job_id),
        )
        self._conn.commit()


def _now_ms() -> int:
    return int(time.time() * 1000)


def _envelope(
    body: dict[str, Any], *, created_at_utc_ms: int, stack_id: str
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "schema_id": SCHEMA_ID,
        "schema_version": "1.0.0",
        "producer": "openpine",
        "producer_version": "4.0.2",
        "producer_commit": "feat-5.0-job-v1",
        "stack_id": stack_id,
        "created_at_utc_ms": created_at_utc_ms,
        "serializer_id": SERIALIZER_ID,
        "content_hash_alg": "sha256",
        "content_hash": "sha256:" + ("ab" * 32),
    }
    payload.update(body)
    return _rehash(payload)


def _rehash(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["content_hash"] = content_hash(payload, schema_id=SCHEMA_ID)
    try:
        validate_payload(SCHEMA_ID, payload)
    except SchemaValidationError as exc:
        raise JobV1Error(str(exc)) from exc
    return payload
