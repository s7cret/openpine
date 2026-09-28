"""SQLite job store fails closed on corrupt rows and invalid state transitions."""
from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from openpine.jobs.transactional_store import JobV1Error, JobV1Store


def test_duplicate_job_and_batch_input_rollback_keep_single_durable_row(tmp_path: Path):
    db = tmp_path / "job.sqlite"
    with JobV1Store(db) as store:
        original = store.create(job_id="first", kind="backfill", idempotency_key="stable")
        assert store.create(job_id="different", kind="backfill", idempotency_key="stable")["job_id"] == "first"
        with pytest.raises(JobV1Error, match="job already exists"):
            store.create(job_id="first", kind="backfill")
        assert store.create_batch([]) == []
        with pytest.raises(JobV1Error, match="job already exists"):
            store.create_batch([{"job_id": "second", "kind": "backfill"}, {"job_id": "first", "kind": "backfill"}])
        with pytest.raises(JobV1Error, match="job_id required"):
            store.create_batch([{"job_id": "missing-kind", "kind": "backfill"}, {"job_id": "", "kind": "backfill"}])
        with pytest.raises(JobV1Error, match="unknown kind"):
            store.create_batch([{"job_id": "bad", "kind": "forged"}])
        with pytest.raises(JobV1Error, match="max_retries"):
            store.create_batch([{"job_id": "bad", "kind": "backfill", "max_retries": -1}])
        assert [item["job_id"] for item in store.list_jobs()["items"]] == ["first"]
        assert store.get("first")["content_hash"] == original["content_hash"]
    with JobV1Store(db) as reopened:
        assert [item["job_id"] for item in reopened.list_jobs()["items"]] == ["first"]
        assert reopened.events(after="non-existent") == []
        assert reopened.needs_resync(after="non-existent") is True
        assert reopened.needs_resync(after="") is False


def test_lease_and_state_guards_leave_persisted_job_unchanged(tmp_path: Path):
    with JobV1Store(tmp_path / "jobs.sqlite") as store:
        created = store.create(job_id="job-1", kind="backfill")
        with pytest.raises(JobV1Error, match="lease owner required"):
            store.mark_running("job-1", lease_owner="", lease_deadline_utc_ms=200, now_ms=100)
        with pytest.raises(JobV1Error, match="in the future"):
            store.mark_running("job-1", lease_owner="worker-a", lease_deadline_utc_ms=100, now_ms=100)
        with pytest.raises(JobV1Error, match="version conflict"):
            store.mark_running("job-1", lease_owner="worker-a", lease_deadline_utc_ms=200, now_ms=100, expected_version=77)
        assert store.get("job-1")["version"] == created["version"]
        active = store.mark_running("job-1", lease_owner="worker-a", lease_deadline_utc_ms=200, now_ms=100)
        assert active["state"] == "RUNNING"
        with pytest.raises(JobV1Error, match="in the future"):
            store.renew_lease("job-1", lease_owner="worker-a", lease_deadline_utc_ms=100, now_ms=100)
        with pytest.raises(JobV1Error, match="owner mismatch"):
            store.renew_lease("job-1", lease_owner="other-worker", lease_deadline_utc_ms=220, now_ms=110)
        with pytest.raises(JobV1Error, match="lease expired"):
            store.renew_lease("job-1", lease_owner="worker-a", lease_deadline_utc_ms=250, now_ms=201)
        with pytest.raises(JobV1Error, match="requires a RUNNING job"):
            store.create(job_id="queued", kind="backfill")
            with store.publication_lease("queued", lease_owner="worker-a", now_ms=110):
                raise AssertionError("not reached")
        with pytest.raises(JobV1Error, match="active lease owner required"):
            store.recover_worker_leases(active_lease_owner="", now_ms=210)
        assert store.recover_worker_leases(active_lease_owner="worker-a", now_ms=180) == 0
        assert store.get("job-1")["state"] == "RUNNING"
        assert store.recover_worker_leases(active_lease_owner="worker-a", now_ms=201) == 1
        lost = store.get("job-1")
        assert lost["state"] == "LOST" and lost["lease_owner"] is None
        with pytest.raises(JobV1Error, match="not allowed"):
            store.mark_succeeded("job-1", lease_owner="worker-a", now_ms=220)
        with pytest.raises(JobV1Error, match="not allowed"):
            store.mark_running("job-1", lease_owner="worker-b", lease_deadline_utc_ms=300, now_ms=220)
        assert store.get("job-1")["version"] == lost["version"]


def test_invalid_persisted_row_is_not_silently_repaired_or_returned(tmp_path: Path):
    db = tmp_path / "corrupt.sqlite"
    with JobV1Store(db) as store:
        store.create(job_id="good", kind="backfill")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE jobs SET payload = ? WHERE job_id = ?", ("broken json", "good"))
        conn.commit()
    with pytest.raises(JobV1Error, match="corrupt persisted job"):
        JobV1Store(db)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE jobs SET payload = ? WHERE job_id = ?", (json.dumps({"version": 1, "job_id": "good"}), "good"))
        conn.commit()
    with JobV1Store(db) as store:
        with pytest.raises(JobV1Error):
            store.get("good")


def test_legacy_sqlite_row_migration_preserves_identity_and_detects_corruption(tmp_path: Path):
    db = tmp_path / "legacy.sqlite"
    with JobV1Store(db) as store:
        source = store.create(job_id="historical", kind="backfill", idempotency_key="legacy-key")
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("DROP INDEX ux_jobs_idempotency_key")
        conn.execute("CREATE TABLE jobs_old (job_id TEXT PRIMARY KEY, payload TEXT NOT NULL, version INTEGER NOT NULL)")
        legacy = {key: value for key, value in source.items() if key != "version"}
        conn.execute("INSERT INTO jobs_old VALUES (?, ?, ?)", ("historical", json.dumps(legacy), 7))
        conn.execute("DROP TABLE events")
        conn.execute("DROP TABLE jobs")
        conn.execute("ALTER TABLE jobs_old RENAME TO jobs")
        conn.commit()
    with JobV1Store(db) as reopened:
        restored = reopened.get("historical")
        assert restored["version"] == 7
        assert restored["idempotency_key"] == "legacy-key"
        assert reopened.create(job_id="duplicate", kind="backfill", idempotency_key="legacy-key")["job_id"] == "historical"
        with pytest.raises(JobV1Error, match="job_id required"):
            reopened.get("")
        with pytest.raises(JobV1Error, match="job not found"):
            reopened.get("absent")
        with pytest.raises(JobV1Error, match="max_retries"):
            reopened.create(job_id="invalid", kind="backfill", max_retries=-1)
    with closing(sqlite3.connect(db)) as conn:
        conn.execute("UPDATE jobs SET payload = ? WHERE job_id = ?", ("{invalid", "historical"))
        conn.commit()
    with pytest.raises(JobV1Error, match="corrupt persisted job"):
        JobV1Store(db)
