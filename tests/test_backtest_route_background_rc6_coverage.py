"""RC6 backtest route/background state uses SQLite and explicit terminal boundaries."""
from __future__ import annotations

import asyncio
import copy
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks, HTTPException
from marketdata_provider.contracts import (
    Bar,
    BarQuery,
    BarSeries,
    CoverageReport,
    InstrumentKey,
    parse_timeframe,
)

from openpine.gateway.routes import backtest
from openpine.gateway.side_effects import require_http_admit as genuine_require_http_admit
from openpine.gateway.schemas import BacktestEstimateResponse, BacktestRunRequest
from openpine.storage.backtest_dto import BacktestRunRequest as StoredBacktestRequest
from openpine.storage.backtest_storage import BacktestResultStore
from openpine.storage.migrations import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage
from tests.admission_helpers import make_deployment_identity, make_sealed_artifact
from tests.rc4_fixtures import admitted_manifest, canonical_series


class _Progress:
    def __init__(self) -> None:
        self.events: list[dict[str, object]] = []
        self.broadcasts: list[str] = []

    def update_progress(self, run_id, domain, status, pct, message, detail=None) -> None:
        self.events.append(
            {
                "run_id": run_id,
                "domain": domain,
                "status": status,
                "pct": pct,
                "message": message,
                "detail": detail,
            }
        )

    async def broadcast_progress(self, run_id: str) -> None:
        self.broadcasts.append(run_id)


def _strategy(**overrides) -> SimpleNamespace:
    values = {
        "strategy_id": "strategy-1",
        "pine_id": "pine-1",
        "artifact_id": "artifact-1",
        "params_hash": "sha256:" + "a" * 64,
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "1m",
        "params_json": "{}",
        "semantic_profile": "strict_5x",
        "archived": False,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def _canonical_series() -> object:
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    timeframe = parse_timeframe("1m")
    bars = (
        Bar(instrument, timeframe, 0, 60_000, 10.0, 12.0, 9.0, 11.0, 3.0, True),
        Bar(instrument, timeframe, 60_000, 120_000, 11.0, 13.0, 10.0, 12.0, 4.0, True),
    )
    query = BarQuery(instrument, timeframe, 0, 120_000, gap_policy="allow_with_metadata")
    coverage = CoverageReport(0, 120_000, 0, 120_000, source_mix=("provider",))
    return canonical_series(BarSeries(query, bars, coverage))


@pytest.fixture
def sqlite_backtest_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    storage = SQLiteStorage(tmp_path / "backtest.sqlite")
    MigrationRunner().run_migrations(storage)
    import openpine.storage.backtest_storage as storage_module

    monkeypatch.setattr(
        storage_module.OpenPineConfig, "load", lambda: SimpleNamespace(data_dir=tmp_path)
    )
    store = BacktestResultStore(storage)
    run_id = store.create_run(
        StoredBacktestRequest(
            strategy_id="strategy-1",
            pine_id="pine-1",
            artifact_id="artifact-1",
            params_hash="sha256:" + "a" * 64,
            exchange="binance",
            market_type="spot",
            symbol="BTCUSDT",
            timeframe="1m",
            from_time=0,
            to_time=120_000,
        )
    )
    state = SimpleNamespace(
        storage=storage,
        backtest_store=store,
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        backtest_cancel_requests=set(),
        config=SimpleNamespace(data_dir=tmp_path, data_cache_root=None),
        admission_identity=make_deployment_identity(),
        admitted_manifest=admitted_manifest(),
    )
    try:
        yield state, run_id, storage
    finally:
        storage.close()


def test_invalid_sealed_artifact_fails_real_sqlite_run_without_compute(
    sqlite_backtest_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, run_id, storage = sqlite_backtest_state
    artifact = copy.deepcopy(make_sealed_artifact())
    artifact["python_code"] = str(artifact["python_code"]) + "\n# tampered"
    state.artifact_store = SimpleNamespace(get_artifact=lambda *_: artifact)
    state.orchestrator = SimpleNamespace(
        load_bars=lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("invalid artifact must stop before data load")
        )
    )
    progress = _Progress()
    monkeypatch.setattr(backtest, "ws_manager", progress)

    asyncio.run(
        backtest._run_backtest_background(
            state, "strategy-1", run_id, 0, 120_000, None, 0, False
        )
    )

    row = storage.execute(
        "SELECT status, error_message FROM backtest_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    assert tuple(row) == ("failed", "generated artifact emitted module hash mismatch")
    assert [(event["status"], event["pct"]) for event in progress.events] == [
        ("running", 0.0),
        ("running", 0.1),
        ("failed", 0.1),
    ]
    assert progress.broadcasts == [run_id, run_id]


def test_data_load_progress_then_cancel_persists_cancelled_without_fingerprint(
    sqlite_backtest_state, monkeypatch: pytest.MonkeyPatch
) -> None:
    state, run_id, storage = sqlite_backtest_state
    state.artifact_store = SimpleNamespace(get_artifact=lambda *_: make_sealed_artifact())
    series = _canonical_series()

    def load_bars(query, *, progress_callback):
        progress_callback(2, 1, 2, 1, 0, "provider")
        state.backtest_cancel_requests.add(run_id)
        return series

    state.orchestrator = SimpleNamespace(load_bars=load_bars)
    progress = _Progress()
    monkeypatch.setattr(backtest, "ws_manager", progress)

    asyncio.run(
        backtest._run_backtest_background(
            state, "strategy-1", run_id, 0, 120_000, None, 0, False
        )
    )

    row = storage.execute(
        "SELECT status, error_message, data_fingerprint FROM backtest_runs WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert tuple(row) == ("cancelled", "Cancelled during market data load", None)
    loading = [event for event in progress.events if event["message"].startswith("Loading bars")]
    assert len(loading) == 1
    assert loading[0]["detail"] == {
        "phase": "provider",
        "bars_processed": 2,
        "total_bars": 2,
        "pages_processed": 1,
        "total_pages": 1,
        "requested_from": 0,
        "effective_from": 0,
        "earliest_available": 0,
        "adjusted": False,
    }
    assert progress.events[-1]["status"] == "cancelled"
    assert run_id not in state.backtest_cancel_requests


def test_fingerprint_helper_adds_schema_once_and_persists_value(
    sqlite_backtest_state,
) -> None:
    state, run_id, storage = sqlite_backtest_state

    backtest._save_backtest_data_fingerprint(state, run_id, "sha256:independent-data")
    backtest._save_backtest_data_fingerprint(state, run_id, "sha256:replacement-data")

    row = storage.execute(
        "SELECT data_fingerprint FROM backtest_runs WHERE run_id = ?", (run_id,)
    ).fetchone()
    columns = storage.execute("PRAGMA table_info(backtest_runs)").fetchall()
    indexes = storage.execute("PRAGMA index_list(backtest_runs)").fetchall()
    assert row[0] == "sha256:replacement-data"
    assert sum(column[1] == "data_fingerprint" for column in columns) == 1
    assert any(index[1] == "idx_backtest_runs_data_fingerprint" for index in indexes)


class _CreateFailStore:
    def __init__(self) -> None:
        self.create_attempts = 0

    def create_run(self, _request) -> str:
        self.create_attempts += 1
        raise RuntimeError("SQLite write lock")


class _JobStore:
    def __init__(self) -> None:
        self.created: list[dict[str, object]] = []

    def create(self, **kwargs):
        self.created.append(kwargs)
        return kwargs


def _route_state(storage, store, job_store=None) -> SimpleNamespace:
    return SimpleNamespace(
        storage=storage,
        backtest_store=store,
        job_store=job_store or _JobStore(),
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: _strategy()),
        backtest_cancel_requests=set(),
        admission_identity=make_deployment_identity(),
        admitted_manifest=admitted_manifest(),
    )


def _estimate() -> BacktestEstimateResponse:
    return BacktestEstimateResponse(
        strategy_id="strategy-1",
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


def test_route_releases_real_idempotency_claim_after_run_storage_failure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = SQLiteStorage(tmp_path / "idempotency.sqlite")
    MigrationRunner().run_migrations(storage)
    state = _route_state(storage, _CreateFailStore())
    body = BacktestRunRequest(strategy_id="strategy-1", from_time="0", to_time="120000")
    monkeypatch.setattr(backtest, "_estimate_backtest_market_data", lambda *_: _estimate())
    try:
        with pytest.raises(HTTPException) as failed:
            asyncio.run(
                backtest.run_backtest(
                    body, BackgroundTasks(), state, idempotency_key="storage-failure-key"
                )
            )
        assert failed.value.status_code == 503
        assert failed.value.detail == "Backtest storage is unavailable"
        assert state.backtest_store.create_attempts == 1
        row = storage.execute(
            "SELECT count(*) FROM api_idempotency WHERE scope = ? AND idempotency_key = ?",
            ("backtest.run", "storage-failure-key"),
        ).fetchone()
        assert row[0] == 0
        lease = backtest._backtest_admission_limiter(state).try_acquire()
        assert lease is not None
        lease.release()
    finally:
        storage.close()


def test_route_rejects_missing_deployment_identity_before_store_or_job_write(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    storage = SQLiteStorage(tmp_path / "admission.sqlite")
    MigrationRunner().run_migrations(storage)
    store = _CreateFailStore()
    state = _route_state(storage, store)
    del state.admission_identity
    body = BacktestRunRequest(strategy_id="strategy-1", from_time="0", to_time="120000")
    monkeypatch.setattr(backtest, "require_http_admit", genuine_require_http_admit)
    try:
        with pytest.raises(HTTPException) as rejected:
            asyncio.run(backtest.run_backtest(body, BackgroundTasks(), state))
        assert rejected.value.status_code == 503
        assert rejected.value.detail == "ADMISSION_IDENTITY_REQUIRED"
        assert store.create_attempts == 0
        assert state.job_store.created == []
    finally:
        storage.close()
