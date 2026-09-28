"""SQLite-backed parent-route boundaries for backtest admission, sealing, and export."""
from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException

from openpine.gateway.routes import backtest
from openpine.gateway.schemas import BacktestEstimateResponse, BacktestRunRequest
from openpine.storage.backtest_dto import BacktestRunRequest as StoredBacktestRequest
from openpine.storage.backtest_storage import BacktestResultStore
from openpine.storage.migrations import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage
from tests.admission_helpers import make_deployment_identity, make_sealed_artifact
from tests.rc4_fixtures import admitted_manifest


class _Progress:
    def __init__(self) -> None:
        self.events: list[tuple[str, str, float, str]] = []
        self.broadcasts: list[str] = []

    def update_progress(self, run_id, _domain, status, pct, message, detail=None) -> None:
        del detail
        self.events.append((run_id, status, pct, message))

    async def broadcast_progress(self, run_id: str) -> None:
        self.broadcasts.append(run_id)


class _JobStore:
    def __init__(self) -> None:
        self.records: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.records.append(kwargs)
        return kwargs


class _SchedulingFailure:
    def add_task(self, *_args, **_kwargs) -> None:
        raise RuntimeError("executor queue is closed")


def _strategy(**overrides) -> SimpleNamespace:
    values = {
        "strategy_id": "route-parent",
        "pine_id": "pine-parent",
        "artifact_id": "artifact-parent",
        "params_hash": "sha256:" + "c" * 64,
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "params_json": "{}",
        "semantic_profile": "strict_5x",
        "archived": False,
        "name": "Route Parent",
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _request() -> BacktestRunRequest:
    return BacktestRunRequest(
        strategy_id="route-parent", from_time="0", to_time="120000"
    )


def _stored_request(strategy_id: str = "route-parent") -> StoredBacktestRequest:
    return StoredBacktestRequest(
        strategy_id=strategy_id,
        pine_id="pine-parent",
        artifact_id="artifact-parent",
        params_hash="sha256:" + "c" * 64,
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        from_time=0,
        to_time=120_000,
    )


def _estimate() -> BacktestEstimateResponse:
    return BacktestEstimateResponse(
        strategy_id="route-parent",
        symbol="BTCUSDT",
        timeframe="1m",
        requested_from=0,
        requested_to=120_000,
        effective_from=0,
        effective_to=120_000,
        earliest_available=0,
        adjusted=False,
        estimated_bars=2,
        estimated_pages=1,
    )


@pytest.fixture
def sqlite_route_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage = SQLiteStorage(tmp_path / "parent-route.sqlite")
    MigrationRunner().run_migrations(storage)
    import openpine.storage.backtest_storage as storage_module

    monkeypatch.setattr(
        storage_module.OpenPineConfig, "load", lambda: SimpleNamespace(data_dir=tmp_path)
    )
    store = BacktestResultStore(storage)
    registry = SimpleNamespace(get_strategy=lambda _strategy_id: _strategy())
    state = SimpleNamespace(
        storage=storage,
        backtest_store=store,
        strategy_registry=registry,
        job_store=_JobStore(),
        backtest_cancel_requests=set(),
        config=SimpleNamespace(data_dir=tmp_path, data_cache_root=None),
        admission_identity=make_deployment_identity(),
        admitted_manifest=admitted_manifest(),
    )
    try:
        yield state, storage
    finally:
        storage.close()


def test_saturated_route_releases_sqlite_idempotency_claim_and_never_creates_run(
    sqlite_route_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, storage = sqlite_route_state
    monkeypatch.setattr(backtest, "_estimate_backtest_market_data", lambda *_: _estimate())
    limiter = backtest._backtest_admission_limiter(state)
    first_lease = limiter.try_acquire()
    lease = limiter.try_acquire()
    assert first_lease is not None and lease is not None
    try:
        with pytest.raises(HTTPException) as rejected:
            asyncio.run(
                backtest.run_backtest(
                    _request(),
                    BackgroundTasks(),
                    state,
                    idempotency_key="saturated-parent-key",
                )
            )
        assert rejected.value.status_code == 429
        assert rejected.value.headers["Retry-After"] == "5"
        assert storage.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 0
        assert storage.execute(
            "SELECT count(*) FROM api_idempotency WHERE idempotency_key = ?",
            ("saturated-parent-key",),
        ).fetchone()[0] == 0
    finally:
        lease.release()
        first_lease.release()


def test_route_replays_only_a_persisted_sqlite_run_for_matching_idempotency_key(
    sqlite_route_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, storage = sqlite_route_state
    body = _request()
    run_id = state.backtest_store.create_run(_stored_request())
    request_hash = backtest._backtest_request_hash(body)
    claim = backtest._claim_backtest_idempotency(state, "replay-parent-key", request_hash)
    assert claim.claim_token is not None
    backtest._complete_backtest_idempotency(
        state, "replay-parent-key", request_hash, claim.claim_token, run_id
    )
    monkeypatch.setattr(backtest, "_estimate_backtest_market_data", lambda *_: _estimate())

    response = asyncio.run(
        backtest.run_backtest(
            body, BackgroundTasks(), state, idempotency_key="replay-parent-key"
        )
    )

    assert (response.run_id, response.strategy_id, response.status) == (
        run_id,
        "route-parent",
        "running",
    )
    assert storage.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 1
    assert state.job_store.records == []


def test_schedule_failure_marks_real_run_failed_after_admitted_job_persistence(
    sqlite_route_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, storage = sqlite_route_state
    monkeypatch.setattr(backtest, "_estimate_backtest_market_data", lambda *_: _estimate())

    with pytest.raises(HTTPException) as failed:
        asyncio.run(backtest.run_backtest(_request(), _SchedulingFailure(), state))

    assert failed.value.status_code == 503
    assert failed.value.detail == "Unable to schedule backtest"
    row = storage.execute(
        "SELECT status, error_message FROM backtest_runs"
    ).fetchone()
    assert tuple(row) == ("failed", "Failed to schedule backtest")
    assert len(state.job_store.records) == 1
    assert "semantic_profile:strict_5x" in state.job_store.records[0]["input_artifact_refs"]
    lease = backtest._backtest_admission_limiter(state).try_acquire()
    assert lease is not None
    lease.release()


def test_background_missing_strategy_seals_and_persists_terminal_failure(
    sqlite_route_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, storage = sqlite_route_state
    run_id = state.backtest_store.create_run(_stored_request())
    state.strategy_registry = SimpleNamespace(
        get_strategy=lambda _strategy_id: (_ for _ in ()).throw(KeyError("gone"))
    )
    progress = _Progress()
    monkeypatch.setattr(backtest, "ws_manager", progress)
    try:
        asyncio.run(
            backtest._run_backtest_background(
                state, "route-parent", run_id, 0, 120_000, None, 0, False
            )
        )
        row = storage.execute(
            "SELECT status, error_message FROM backtest_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert tuple(row) == ("failed", "Strategy not found")
        assert progress.events == [
            (run_id, "running", 0.0, "Loading strategy..."),
            (run_id, "failed", 0.0, "Strategy not found"),
        ]
        assert progress.broadcasts == [run_id]
        assert backtest._backtest_terminal_outcome(run_id) is None
    finally:
        with backtest._ACTIVE_BACKTEST_WORKERS_LOCK:
            backtest._TERMINAL_BACKTEST_RUNS.discard(run_id)
            backtest._TERMINAL_BACKTEST_OUTCOMES.pop(run_id, None)


def test_cancelled_data_load_seal_persists_once_and_clears_request(
    sqlite_route_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, storage = sqlite_route_state
    run_id = state.backtest_store.create_run(_stored_request())
    state.artifact_store = SimpleNamespace(get_artifact=lambda *_args: make_sealed_artifact())
    state.orchestrator = SimpleNamespace(
        load_bars=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("cancel must seal before data loading")
        )
    )
    state.backtest_cancel_requests.add(run_id)
    progress = _Progress()
    monkeypatch.setattr(backtest, "ws_manager", progress)
    try:
        asyncio.run(
            backtest._run_backtest_background(
                state, "route-parent", run_id, 0, 120_000, None, 0, False
            )
        )
        row = storage.execute(
            "SELECT status, error_message FROM backtest_runs WHERE run_id = ?", (run_id,)
        ).fetchone()
        assert tuple(row) == ("cancelled", "Cancelled during strategy load")
        assert progress.events[-1] == (
            run_id,
            "cancelled",
            0.0,
            "Cancelled during strategy load",
        )
        assert run_id not in state.backtest_cancel_requests
        assert backtest._backtest_terminal_outcome(run_id) == "cancelled"
    finally:
        with backtest._ACTIVE_BACKTEST_WORKERS_LOCK:
            backtest._TERMINAL_BACKTEST_RUNS.discard(run_id)
            backtest._TERMINAL_BACKTEST_OUTCOMES.pop(run_id, None)


def test_export_omits_unsafe_sqlite_artifact_filename_without_leaking_server_path(
    sqlite_route_state, tmp_path: Path
) -> None:
    state, storage = sqlite_route_state
    run_id = state.backtest_store.create_run(_stored_request())
    outside = tmp_path / "outside-parent-route.md"
    outside.write_text("private", encoding="utf-8")
    storage.execute(
        """
        INSERT INTO backtest_artifacts
        (artifact_row_id, run_id, strategy_id, artifact_type, path, format, created_at)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        ("unsafe-artifact", run_id, "route-parent", "report_md", str(outside), "markdown", 1),
    )
    storage.commit()

    exported = asyncio.run(backtest.export_run(run_id, state))

    assert exported["run_id"] == run_id
    assert exported["trades"] == []
    assert exported["artifacts"] == [{"type": "report_md"}]
    assert str(outside) not in repr(exported)
    with pytest.raises(HTTPException) as unavailable:
        asyncio.run(backtest.get_run_report(run_id, state))
    assert unavailable.value.status_code == 404


def test_request_semantic_rejection_precedes_sqlite_run_and_job_creation(
    sqlite_route_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, storage = sqlite_route_state
    monkeypatch.setattr(backtest, "_estimate_backtest_market_data", lambda *_: _estimate())
    rejected_body = BacktestRunRequest(
        strategy_id="route-parent",
        from_time="0",
        to_time="120000",
        semantic_profile="unsupported-parent-profile",
    )

    with pytest.raises(HTTPException) as rejected:
        asyncio.run(backtest.run_backtest(rejected_body, BackgroundTasks(), state))

    assert rejected.value.status_code == 403
    assert storage.execute("SELECT count(*) FROM backtest_runs").fetchone()[0] == 0
    assert state.job_store.records == []


def test_estimate_and_action_errors_preserve_sqlite_run_ownership(
    sqlite_route_state,
) -> None:
    state, storage = sqlite_route_state
    run_id = state.backtest_store.create_run(_stored_request())

    with pytest.raises(HTTPException) as unsupported:
        asyncio.run(backtest.run_action(run_id, "restart", state))
    assert unsupported.value.status_code == 400
    assert state.backtest_cancel_requests == set()

    with pytest.raises(HTTPException) as missing_action:
        asyncio.run(backtest.run_action("no-parent-run", "cancel", state))
    assert missing_action.value.status_code == 404

    state.strategy_registry = SimpleNamespace(
        get_strategy=lambda _strategy_id: _strategy(archived=True)
    )
    with pytest.raises(HTTPException) as archived:
        asyncio.run(backtest.estimate_backtest("route-parent", "0", "120000", state))
    assert archived.value.status_code == 400

    with pytest.raises(HTTPException) as invalid_range:
        asyncio.run(backtest.estimate_backtest("route-parent", "120000", "0", state))
    assert invalid_range.value.status_code == 400
    assert storage.execute(
        "SELECT status FROM backtest_runs WHERE run_id = ?", (run_id,)
    ).fetchone()[0] == "running"


def test_absent_sqlite_run_is_rejected_by_each_parent_result_route(
    sqlite_route_state,
) -> None:
    state, _storage = sqlite_route_state
    missing = "no-parent-run"
    routes = (
        lambda: backtest.get_run(missing, state),
        lambda: backtest.delete_run(missing, state),
        lambda: backtest.get_run_trades(missing, state),
        lambda: backtest.get_run_equity(missing, state),
        lambda: backtest.get_run_plots(missing, state),
        lambda: backtest.get_run_bar_outputs(missing, state),
        lambda: backtest.get_run_report(missing, state),
        lambda: backtest.export_run(missing, state),
    )

    for route in routes:
        with pytest.raises(HTTPException) as missing_run:
            asyncio.run(route())
        assert missing_run.value.status_code == 404
