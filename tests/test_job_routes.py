from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openpine.gateway.deps import get_state
from openpine.gateway.routes.jobs import router
from openpine.jobs.persist import JobV1Store


def _client(tmp_path) -> tuple[TestClient, JobV1Store]:
    store = JobV1Store(tmp_path / "jobs.sqlite")
    app = FastAPI()
    app.include_router(router, prefix="/api")
    app.dependency_overrides[get_state] = lambda: SimpleNamespace(job_store=store)
    return TestClient(app), store


def test_http_get_missing_job_is_404(tmp_path) -> None:
    client, _store = _client(tmp_path)
    response = client.get("/api/jobs/does-not-exist")
    assert response.status_code == 404


def test_http_list_detail_cancel_retry_and_events(tmp_path) -> None:
    client, store = _client(tmp_path)
    store.create(job_id="job-a", kind="backtest")
    store.create(job_id="job-b", kind="compile")
    listed = client.get("/api/jobs", params={"kind": "backtest"})
    assert listed.status_code == 200
    assert listed.json()["items"][0]["job_id"] == "job-a"
    detail = client.get("/api/jobs/job-a")
    assert detail.status_code == 200
    assert detail.json()["kind"] == "backtest"
    store.mark_failed("job-b", error_code="x")
    canceled = client.post("/api/jobs/job-a/cancel", headers={"Idempotency-Key": "c1"})
    again = client.post("/api/jobs/job-a/cancel", headers={"Idempotency-Key": "c1"})
    assert canceled.json()["state"] == "CANCELED"
    assert again.json()["event_cursor"] == canceled.json()["event_cursor"]
    retried = client.post("/api/jobs/job-b/retry")
    assert retried.json()["state"] == "QUEUED"
    events = client.get("/api/jobs/events", params={"after": "nope"})
    assert events.json()["resync"] is True


def test_live_admission_is_get_and_non_mutating() -> None:
    from openpine.gateway.routes.trading import router as trading_router

    app = FastAPI()
    app.include_router(trading_router)
    client = TestClient(app)
    response = client.get("/live/admission")
    assert response.status_code == 200
    body = response.json()
    assert body["mutating"] is False
    assert "admitted" in body
    assert client.post("/live/admission").status_code == 405
