from __future__ import annotations

import threading
from pathlib import Path

import pytest

from openpine.jobs.persist import JobV1Error, JobV1Store


def _create(store: JobV1Store, job_id: str, *, key: str | None = None) -> dict:
    return store.create(
        job_id=job_id,
        kind="backtest",
        actor="test",
        idempotency_key=key,
        input_artifact_refs=["semantic_profile:strict_5x"],
    )


def test_job_store_finalizer_is_idempotent(tmp_path: Path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    store.close()
    store.close()
    store.__del__()
    assert store._closed is True


def test_job_store_enables_wal_busy_timeout_and_versioned_payload(tmp_path: Path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    try:
        job = _create(store, "job-1")
        assert job["version"] == 1
        assert store._conn.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
        assert store._conn.execute("PRAGMA busy_timeout").fetchone()[0] >= 5_000
    finally:
        store.close()


def test_state_and_event_append_rollback_together(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    try:
        _create(store, "job-1")

        def fail_event(*_args, **_kwargs):
            raise RuntimeError("event write failed")

        monkeypatch.setattr(store, "_append_event_in_transaction", fail_event)
        with pytest.raises(RuntimeError, match="event write failed"):
            store.mark_running(
                "job-1",
                lease_owner="worker-1",
                lease_deadline_utc_ms=10_000,
                now_ms=1_000,
            )

        job = store.get("job-1")
        assert job["state"] == "QUEUED"
        assert job["version"] == 1
    finally:
        store.close()


def test_transition_cas_and_matching_live_lease_owner(tmp_path: Path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    try:
        _create(store, "job-1")
        with pytest.raises(JobV1Error, match="transition"):
            store.mark_succeeded("job-1", lease_owner="worker-1", expected_version=1)

        running = store.mark_running(
            "job-1",
            lease_owner="worker-1",
            lease_deadline_utc_ms=10_000,
            now_ms=1_000,
            expected_version=1,
        )
        assert running["version"] == 2

        with pytest.raises(JobV1Error, match="owner"):
            store.renew_lease(
                "job-1",
                lease_owner="worker-2",
                lease_deadline_utc_ms=20_000,
                now_ms=2_000,
                expected_version=2,
            )
        renewed = store.renew_lease(
            "job-1",
            lease_owner="worker-1",
            lease_deadline_utc_ms=20_000,
            now_ms=2_000,
            expected_version=2,
        )
        assert renewed["version"] == 3

        with pytest.raises(JobV1Error, match="expired"):
            store.mark_succeeded(
                "job-1",
                lease_owner="worker-1",
                expected_version=3,
                now_ms=20_001,
            )
        succeeded = store.mark_succeeded(
            "job-1",
            lease_owner="worker-1",
            expected_version=3,
            now_ms=3_000,
            result_artifact_refs=["result:1"],
        )
        assert succeeded["state"] == "SUCCEEDED"
        assert succeeded["version"] == 4
    finally:
        store.close()


def test_unique_idempotency_key_is_atomic_under_threads(tmp_path: Path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    results: list[dict] = []
    errors: list[BaseException] = []

    def create(job_id: str) -> None:
        try:
            results.append(_create(store, job_id, key="same-key"))
        except BaseException as exc:  # noqa: BLE001 - test captures thread failures
            errors.append(exc)

    try:
        threads = [threading.Thread(target=create, args=(f"job-{index}",)) for index in range(8)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join()

        assert errors == []
        assert len(results) == 8
        assert {job["job_id"] for job in results} == {results[0]["job_id"]}
        indexes = store._conn.execute("PRAGMA index_list(jobs)").fetchall()
        assert any(row[2] for row in indexes)
    finally:
        store.close()


def test_recovery_processes_every_expired_lease_beyond_200(tmp_path: Path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    try:
        for index in range(205):
            job_id = f"job-{index:03d}"
            _create(store, job_id)
            store.mark_running(
                job_id,
                lease_owner="worker-1",
                lease_deadline_utc_ms=10,
                now_ms=0,
                expected_version=1,
            )

        assert store.recover_lost_leases(now_ms=11) == 205
        assert store.list_jobs(state="LOST", limit=250)["items"]
    finally:
        store.close()


def test_event_cursor_is_db_monotonic_not_timestamp_identity(tmp_path: Path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    try:
        created = _create(store, "job-1")
        running = store.mark_running(
            "job-1",
            lease_owner="worker-1",
            lease_deadline_utc_ms=10_000,
            now_ms=1_000,
            expected_version=1,
        )
        rows = store.events()
        assert [row["sequence"] for row in rows] == [1, 2]
        assert created["event_cursor"] != running["event_cursor"]
        assert all("1000" not in row["event_id"] for row in rows)
    finally:
        store.close()
