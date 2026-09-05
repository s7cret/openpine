from __future__ import annotations

from types import SimpleNamespace

from fastapi import FastAPI
from fastapi.testclient import TestClient

from openpine.admission import DeploymentAdmissionIdentity
from openpine.gateway.deps import get_state
from openpine.gateway.routes.jobs import router
from openpine.jobs.persist import JobV1Store

STACK_HASH = "sha256:" + "e" * 64


def _admission_identity() -> DeploymentAdmissionIdentity:
    return DeploymentAdmissionIdentity(
        stack_id="test-stack",
        stack_manifest_hash=STACK_HASH,
        wheel_identities=(("openpine", "5.0.0rc4", "sha256:" + "a" * 64),),
        schema_hashes={"openpine.run.v2": "sha256:" + "b" * 64},
        capabilities=frozenset({"closed_bar"}),
        semantic_profiles=frozenset({"strict_5x"}),
        finality_policies=frozenset({"CLOSED_BAR_ONLY"}),
        warmup_policies=frozenset({"CALC_ONLY"}),
        score_policies=frozenset({"ALL_BARS"}),
    )


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


def test_compare_jobs_warns_on_profile_mismatch(tmp_path) -> None:
    client, store = _client(tmp_path)
    store.create(
        job_id="job-legacy",
        kind="backtest",
        input_artifact_refs=["semantic_profile:legacy_4x"],
    )
    store.create(
        job_id="job-strict",
        kind="backtest",
        input_artifact_refs=["semantic_profile:strict_5x"],
    )
    response = client.get(
        "/api/jobs/compare", params={"left": "job-legacy", "right": "job-strict"}
    )
    assert response.status_code == 200
    body = response.json()
    assert body["warning"] is True
    assert body["code"] == "SEMANTIC_PROFILE_MISMATCH"
    assert body["ok"] is False


def test_live_admission_is_get_and_non_mutating() -> None:
    from openpine.gateway.routes.trading import router as trading_router

    app = FastAPI()
    app.include_router(trading_router)
    app.dependency_overrides[get_state] = lambda: SimpleNamespace(
        admission_identity=_admission_identity()
    )
    client = TestClient(app)
    response = client.get("/live/admission")
    assert response.status_code == 200
    body = response.json()
    assert body["mutating"] is False
    assert "admitted" in body
    assert client.post("/live/admission").status_code == 405


def test_live_start_without_typed_confirm_is_400(monkeypatch) -> None:
    from openpine.gateway.routes.trading import router as trading_router

    monkeypatch.setattr("openpine.live_release_gate.LIVE_RELEASE_ENABLED", True)
    app = FastAPI()
    app.include_router(trading_router)
    app.dependency_overrides[get_state] = lambda: SimpleNamespace(
        config=SimpleNamespace(live_enabled=True),
        admission_identity=_admission_identity(),
    )
    client = TestClient(app)
    preview = client.get("/live/admission/preview", params={"strategy_id": "s1"})
    assert preview.status_code == 200
    assert preview.json()["mutating"] is False
    denied = client.post("/live/start", json={"strategy_id": "s1"})
    assert denied.status_code == 400


def test_live_and_paper_start_use_stored_semantic_profile(monkeypatch) -> None:
    import time

    from openpine.gateway.routes.trading import router as trading_router
    from openpine.live_preview import make_live_preview

    monkeypatch.setattr("openpine.live_release_gate.LIVE_RELEASE_ENABLED", True)
    strategy = SimpleNamespace(
        status="paused",
        archived=False,
        mode="paper",
        semantic_profile="strict_5x",
    )
    registry = SimpleNamespace(
        get_strategy=lambda strategy_id: strategy,
        activate_strategy=lambda *args, **kwargs: None,
    )
    app = FastAPI()
    app.include_router(trading_router)
    app.dependency_overrides[get_state] = lambda: SimpleNamespace(
        config=SimpleNamespace(live_enabled=True),
        strategy_registry=registry,
        admission_identity=_admission_identity(),
    )
    client = TestClient(app)
    preview = make_live_preview(
        "s1", now_ms=int(time.time() * 1000), stack_id=STACK_HASH
    )
    live_payload = {
        "strategy_id": "s1",
        "preview_hash": preview["preview_hash"],
        "confirmation": "LIVE",
        "idempotency_key": "live-s1",
        "expires_at_utc_ms": preview["expires_at_utc_ms"],
    }
    automatic = client.post("/live/start", json=live_payload)
    assert automatic.status_code == 200
    assert automatic.json()["mode"] == "live"
    legacy = client.post("/live/start", json={**live_payload, "semantic_profile": "legacy_4x"})
    assert legacy.status_code == 403
    assert "immutable" in legacy.json()["detail"].lower()
    paper = client.post("/paper/start", json={"strategy_id": "s1"})
    assert paper.status_code == 200
    assert paper.json()["mode"] == "paper"
