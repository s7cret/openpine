from __future__ import annotations

import pytest
from openpine_contracts import JobState, validate_payload

from openpine.jobs.persist import JOB_KINDS, JobV1Error, JobV1Store


def test_create_requires_stable_job_id(tmp_path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    with pytest.raises(JobV1Error, match="job_id required"):
        store.create(job_id="", kind="backtest")
    with pytest.raises(JobV1Error, match="job_id required"):
        store.create(job_id=None, kind="backtest")  # type: ignore[arg-type]


def test_unknown_kind_and_state_fail_closed(tmp_path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    with pytest.raises(JobV1Error, match="unknown kind"):
        store.create(job_id="j1", kind="unknown")
    job = store.create(job_id="j1", kind="compile")
    validate_payload("openpine.job.v1", job)
    assert job["state"] == JobState.QUEUED
    assert JOB_KINDS == (
        "backtest",
        "backfill",
        "compile",
        "optimize",
        "parity",
        "report",
    )


def test_payload_uses_utc_ms_not_iso(tmp_path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    job = store.create(job_id="j-iso", kind="parity", actor="tester")
    assert isinstance(job["created_at_utc_ms"], int)
    assert isinstance(job["updated_at_utc_ms"], int)
    assert job["updated_at_utc_ms"] >= job["created_at_utc_ms"]
    assert "T" not in str(job["created_at_utc_ms"])


def test_survives_reopen_and_missing_id_is_error(tmp_path) -> None:
    path = tmp_path / "jobs.sqlite"
    first = JobV1Store(path)
    first.create(job_id="stable-1", kind="backfill", parent_job_id=None)
    first.create(
        job_id="child-1",
        kind="backfill",
        parent_job_id="stable-1",
    )
    second = JobV1Store(path)
    loaded = second.get("stable-1")
    assert loaded["job_id"] == "stable-1"
    assert "child-1" in loaded["child_job_ids"]
    with pytest.raises(JobV1Error, match="not found"):
        second.get("missing")


def test_list_filters_and_cursor(tmp_path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    for index in range(3):
        store.create(job_id=f"bt-{index}", kind="backtest")
    store.create(job_id="opt-1", kind="optimize")
    page = store.list_jobs(kind="backtest", limit=2)
    assert len(page["items"]) == 2
    assert page["cursor"] is not None
    rest = store.list_jobs(kind="backtest", cursor=page["cursor"], limit=2)
    assert len(rest["items"]) == 1
    assert {item["job_id"] for item in page["items"] + rest["items"]} == {
        "bt-0",
        "bt-1",
        "bt-2",
    }


def test_cancel_retry_and_lease_recovery_are_idempotent(tmp_path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    store.create(job_id="c1", kind="backtest", idempotency_key="same")
    again = store.create(job_id="c1-dup", kind="backtest", idempotency_key="same")
    assert again["job_id"] == "c1"
    first = store.cancel("c1", idempotency_key="cancel-1")
    second = store.cancel("c1", idempotency_key="cancel-1")
    assert first["state"] == JobState.CANCELED
    assert second["state"] == JobState.CANCELED
    assert first["event_cursor"] == second["event_cursor"]

    store.create(job_id="f1", kind="optimize")
    store.mark_failed("f1", error_code="boom")
    retried = store.retry("f1")
    assert retried["state"] == JobState.QUEUED
    assert retried["retry_count"] == 1
    store.retry("f1")
    assert store.get("f1")["retry_count"] == 1

    store.create(job_id="run-1", kind="parity")
    store.mark_running("run-1", lease_owner="w1", lease_deadline_utc_ms=1)
    lost = store.recover_lost_leases(now_ms=2)
    assert lost == 1
    assert store.get("run-1")["state"] == JobState.LOST


def test_events_are_ordered_duplicate_safe_and_gap_resyncs(tmp_path) -> None:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    store.create(job_id="e1", kind="compile")
    store.mark_running("e1", lease_owner="w", lease_deadline_utc_ms=10**12)
    store.mark_succeeded("e1", result_artifact_refs=["art:1"])
    events = store.events(after="")
    assert [item["type"] for item in events] == ["created", "running", "succeeded"]
    replay = store.events(after="")
    assert [item["event_id"] for item in replay] == [
        item["event_id"] for item in events
    ]
    gap = store.events(after="999")
    assert gap == []
    assert store.needs_resync(after="999") is True
    assert store.needs_resync(after=events[0]["event_id"]) is False


def test_persist_gateway_job_stamps_semantic_profile(tmp_path) -> None:
    from types import SimpleNamespace

    from openpine.gateway.side_effects import persist_gateway_job

    store = JobV1Store(tmp_path / "jobs.sqlite")
    state = SimpleNamespace(job_store=store)
    persist_gateway_job(
        state,
        job_id="opt-1",
        kind="optimize",
        actor="gateway",
        semantic_profile="legacy_4x",
    )
    persist_gateway_job(state, job_id="cmp-1", kind="compile", actor="gateway")
    persist_gateway_job(
        state,
        job_id="bt-1",
        kind="backtest",
        actor="gateway",
        semantic_profile="strict_5x",
    )
    assert "semantic_profile:legacy_4x" in store.get("opt-1")["input_artifact_refs"]
    compile_refs = store.get("cmp-1")["input_artifact_refs"]
    assert not any(str(item).startswith("semantic_profile:") for item in compile_refs)
    assert "semantic_profile:strict_5x" in store.get("bt-1")["input_artifact_refs"]
    assert "semantic_profile:legacy_4x" not in store.get("bt-1")["input_artifact_refs"]


def test_persist_gateway_job_does_not_silent_legacy(tmp_path) -> None:
    from types import SimpleNamespace

    from openpine.gateway.side_effects import persist_gateway_job
    from openpine.jobs.persist import JobV1Error

    store = JobV1Store(tmp_path / "jobs.sqlite")
    state = SimpleNamespace(job_store=store)
    persist_gateway_job(state, job_id="opt-silent", kind="optimize", actor="gateway")
    persist_gateway_job(state, job_id="fill-1", kind="backfill", actor="gateway")
    with pytest.raises(JobV1Error, match="not found"):
        store.get("opt-silent")
    refs = store.get("fill-1")["input_artifact_refs"]
    assert not any(str(item).startswith("semantic_profile:") for item in refs)
