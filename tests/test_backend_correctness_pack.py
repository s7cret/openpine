from __future__ import annotations

import asyncio
import json
import multiprocessing as mp
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from fastapi import HTTPException

from openpine.gateway.routes import accounts_data
from openpine.gateway.routes import backtest
from openpine.gateway.routes import orders_positions
from openpine.gateway.deps import GatewayState
from openpine.storage import MigrationRunner, SQLiteStorage


class _ImmediateAdapter:
    def run(self, *args, progress_callback=None, **kwargs):
        if progress_callback is not None:
            progress_callback(1, 1)
        return "done"


class _FailingAdapter:
    def run(self, *args, **kwargs):
        raise RuntimeError("worker boom")


def _spawn_result_entry(out, value: str) -> None:
    if hasattr(os, "setsid"):
        os.setsid()
    out.put(("ok", value))


def _delayed_spawn_result_entry(out, value: str, delay: float) -> None:
    time.sleep(delay)
    if hasattr(os, "setsid"):
        os.setsid()
    out.put(("ok", value))


def _sleep_entry(_out, seconds: float) -> None:
    time.sleep(seconds)


class _BlockingTreeAdapter:
    def __init__(self, pid_file: str, *, detached: bool = False) -> None:
        self.pid_file = pid_file
        self.detached = detached

    def run(self, *args, **kwargs):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=self.detached,
        )
        payload = {
            "root_pid": os.getpid(),
            "root_start": _proc_start_time(os.getpid()),
            "child_pid": child.pid,
            "child_start": _proc_start_time(child.pid),
        }
        Path(self.pid_file).write_text(json.dumps(payload), encoding="utf-8")
        while True:
            time.sleep(1)


class _ReturningTreeAdapter:
    def __init__(
        self, pid_file: str, *, detached: bool = False, fail_after_spawn: bool = False
    ) -> None:
        self.pid_file = pid_file
        self.detached = detached
        self.fail_after_spawn = fail_after_spawn

    def run(self, *args, **kwargs):
        child = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=self.detached,
        )
        Path(self.pid_file).write_text(
            json.dumps(
                {
                    "child_pid": child.pid,
                    "child_start": _proc_start_time(child.pid),
                }
            ),
            encoding="utf-8",
        )
        if self.fail_after_spawn:
            raise RuntimeError("adapter failed after spawning child")
        return "done"


def _proc_start_time(pid: int) -> int | None:
    try:
        return int(Path(f"/proc/{pid}/stat").read_text(encoding="utf-8").split()[21])
    except (FileNotFoundError, IndexError, ValueError):
        return None


def _pid_running(pid: int, start_time: int | None) -> bool:
    stat_path = Path(f"/proc/{pid}/stat")
    try:
        fields = stat_path.read_text(encoding="utf-8").split()
    except FileNotFoundError:
        return False
    if len(fields) <= 21 or (start_time is not None and int(fields[21]) != start_time):
        return False
    return fields[2] != "Z"


def _wait_until(predicate, timeout: float = 5.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.02)
    return bool(predicate())


def _kill_owned_pid(pid: int, start_time: int | None) -> None:
    if not _pid_running(pid, start_time):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


class _Progress:
    def __init__(self) -> None:
        self.updates: list[tuple] = []

    def update_progress(self, *args, **kwargs) -> None:
        self.updates.append((args, kwargs))

    async def broadcast_progress(self, run_id: str) -> None:
        return None


class _RunStore:
    def __init__(self, status: str = "running") -> None:
        self.run = SimpleNamespace(run_id="run-owned", status=status)
        self.cancelled: list[tuple[str, str]] = []
        self.created: list[object] = []
        self._counter = 0

    def get_run(self, run_id: str):
        return self.run if run_id == self.run.run_id else None

    def mark_cancelled(self, run_id: str, message: str) -> None:
        self.run.status = "cancelled"
        self.cancelled.append((run_id, message))

    def create_run(self, request: object) -> str:
        self.created.append(request)
        self._counter += 1
        return f"run-{self._counter}"


class _BackgroundTasks:
    def __init__(self) -> None:
        self.calls: list[tuple[object, tuple, dict]] = []

    def add_task(self, fn, *args, **kwargs) -> None:
        self.calls.append((fn, args, kwargs))


@pytest.mark.asyncio
async def test_positions_storage_failures_are_explicit_503() -> None:
    class BrokenStorage:
        def execute(self, *args, **kwargs):
            raise RuntimeError("database offline")

    state = SimpleNamespace(storage=BrokenStorage())

    with pytest.raises(HTTPException) as list_error:
        await orders_positions.list_positions(state=state)
    assert list_error.value.status_code == 503
    assert "unavailable" in str(list_error.value.detail).lower()

    with pytest.raises(HTTPException) as strategy_error:
        await orders_positions.get_strategy_positions("strategy-1", state)
    assert strategy_error.value.status_code == 503
    assert "unavailable" in str(strategy_error.value.detail).lower()


def test_market_aliases_share_one_canonical_series_and_filter_contract() -> None:
    metadata = accounts_data._market_metadata_payload()
    binance = next(item for item in metadata["exchanges"] if item["id"] == "binance")

    assert accounts_data._require_enabled_market_type(binance, "usdm") == "futures"
    assert accounts_data._require_enabled_market_type(binance, "linear") == "futures"

    groups: dict[tuple[str, str, str, str, str], dict[str, object]] = {}
    first = accounts_data._series_entry(
        groups, ("binance", "usdm", "BTCUSDT", "trade", "1m")
    )
    second = accounts_data._series_entry(
        groups, ("binance", "linear", "BTCUSDT", "trade", "1m")
    )

    assert first is second
    assert len(groups) == 1
    assert first["market_type"] == "futures"
    assert accounts_data._series_market_key(first) == ("binance", "futures")


def test_spawn_worker_success_and_failure_always_unregister() -> None:
    assert hasattr(backtest, "_execute_backtest_run_in_thread")
    assert hasattr(backtest, "_active_backtest_worker")

    result = backtest._execute_backtest_run_in_thread(
        "run-success",
        set(),
        _ImmediateAdapter(),
        object,
        [],
        None,
        {},
        None,
        None,
    )
    assert result == "done"
    assert backtest._active_backtest_worker("run-success") is None

    with pytest.raises(RuntimeError, match="worker boom"):
        backtest._execute_backtest_run_in_thread(
            "run-failure",
            set(),
            _FailingAdapter(),
            object,
            [],
            None,
            {},
            None,
            None,
        )
    assert backtest._active_backtest_worker("run-failure") is None


@pytest.mark.parametrize("detached", [False, True])
def test_successful_worker_cleans_up_owned_descendants(
    tmp_path: Path, detached: bool
) -> None:
    pid_file = tmp_path / "returning-tree.json"

    result = backtest._execute_backtest_run_in_thread(
        f"run-returning-tree-{detached}",
        set(),
        _ReturningTreeAdapter(str(pid_file), detached=detached),
        object,
        [],
        None,
        {},
        None,
        None,
    )

    payload = json.loads(pid_file.read_text(encoding="utf-8"))
    child_pid = int(payload["child_pid"])
    child_start = payload["child_start"]
    try:
        assert result == "done"
        assert _wait_until(
            lambda: not _pid_running(child_pid, child_start), timeout=4.0
        )
    finally:
        if _pid_running(child_pid, child_start):
            os.kill(child_pid, signal.SIGKILL)


def test_failed_worker_cleans_up_detached_descendants(tmp_path: Path) -> None:
    pid_file = tmp_path / "failed-tree.json"

    with pytest.raises(RuntimeError, match="adapter failed after spawning child"):
        backtest._execute_backtest_run_in_thread(
            "run-failed-tree",
            set(),
            _ReturningTreeAdapter(
                str(pid_file), detached=True, fail_after_spawn=True
            ),
            object,
            [],
            None,
            {},
            None,
            None,
        )

    payload = json.loads(pid_file.read_text(encoding="utf-8"))
    child_pid = int(payload["child_pid"])
    child_start = payload["child_start"]
    try:
        assert _wait_until(
            lambda: not _pid_running(child_pid, child_start), timeout=4.0
        )
    finally:
        _kill_owned_pid(child_pid, child_start)


def test_artifact_backtest_uses_spawn_context(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    captured: dict[str, object] = {}

    def fake_execute(*args):
        captured["args"] = args
        return "spawned"

    monkeypatch.setattr(backtest, "_execute_backtest_process", fake_execute)
    spec = backtest._ArtifactBacktestSpec(
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
        cache_dir=str(tmp_path),
        exchange="binance",
        market="spot",
        prefetch_end_ms=60_000,
    )

    result = backtest._run_backtest_in_process(
        spec,
        None,
        [],
        SimpleNamespace(),
        {},
        None,
    )

    args = captured["args"]
    assert isinstance(args, tuple)
    assert result == "spawned"
    assert args[2] is backtest._artifact_backtest_process_entry
    assert args[-1] == "spawn"


def test_spawn_process_context_executes_and_unregisters() -> None:
    result = backtest._execute_backtest_process(
        "run-spawn-probe",
        set(),
        _spawn_result_entry,
        ("spawn-ok",),
        None,
        "spawn",
    )

    assert result == "spawn-ok"
    assert backtest._active_backtest_worker("run-spawn-probe") is None


def test_spawn_process_allows_bounded_cold_start_before_isolation() -> None:
    result = backtest._execute_backtest_process(
        "run-delayed-spawn-probe",
        set(),
        _delayed_spawn_result_entry,
        ("spawn-ok", 6.0),
        None,
        "spawn",
    )

    assert result == "spawn-ok"
    assert backtest._active_backtest_worker("run-delayed-spawn-probe") is None


@pytest.mark.parametrize("detached", [False, True])
def test_cancel_action_stops_owned_spawn_worker_tree_before_return(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, detached: bool
) -> None:
    assert hasattr(backtest, "_execute_backtest_run_in_thread")
    assert hasattr(backtest, "_active_backtest_worker")

    progress = _Progress()
    monkeypatch.setattr(backtest, "ws_manager", progress)
    cancel_requests: set[str] = set()
    store = _RunStore()
    state = SimpleNamespace(
        backtest_store=store,
        backtest_cancel_requests=cancel_requests,
    )
    pid_file = tmp_path / "owned-worker-pids.json"
    errors: list[BaseException] = []

    def run_worker() -> None:
        try:
            backtest._execute_backtest_run_in_thread(
                "run-owned",
                cancel_requests,
                _BlockingTreeAdapter(str(pid_file), detached=detached),
                object,
                [],
                None,
                {},
                None,
                None,
            )
        except Exception as exc:  # noqa: BLE001 - thread outcome is asserted below
            errors.append(exc)

    thread = threading.Thread(target=run_worker, daemon=True)
    thread.start()
    payload: dict[str, int | None] = {}
    try:
        assert _wait_until(pid_file.exists, timeout=8.0)
        payload = json.loads(pid_file.read_text(encoding="utf-8"))
        assert _pid_running(int(payload["root_pid"]), payload["root_start"])
        assert _pid_running(int(payload["child_pid"]), payload["child_start"])

        started = time.monotonic()
        response = asyncio.run(backtest.run_action("run-owned", "cancel", state))
        elapsed = time.monotonic() - started

        thread.join(timeout=4.0)
        assert response["accepted"] is True
        assert response["status"] == "cancelled"
        assert elapsed < 4.0
        assert not thread.is_alive()
        assert not _pid_running(int(payload["root_pid"]), payload["root_start"])
        assert not _pid_running(int(payload["child_pid"]), payload["child_start"])
        assert store.cancelled == [("run-owned", "Cancelled during compute")]
        assert backtest._active_backtest_worker("run-owned") is None
        assert errors and errors[0].__class__.__name__ == "_BacktestCancelled"
    finally:
        if payload:
            _kill_owned_pid(int(payload["child_pid"]), payload["child_start"])
            _kill_owned_pid(int(payload["root_pid"]), payload["root_start"])
        thread.join(timeout=2.0)


def test_cancel_waits_for_starting_worker_registration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(backtest, "ws_manager", _Progress())
    ctx = mp.get_context("spawn")
    out = ctx.Queue()
    cleanup_complete = ctx.Event()
    process = ctx.Process(
        target=backtest._supervised_backtest_process_entry,
        args=(out, _sleep_entry, (60.0,), cleanup_complete),
    )
    process.start()
    deadline = time.monotonic() + 5.0
    identity = backtest._proc_identity(process.pid)
    while (
        identity is not None
        and identity[1] != process.pid
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
        identity = backtest._proc_identity(process.pid)
    assert identity is not None
    assert identity[1] == process.pid
    worker = backtest._BacktestWorker(
        process=process,
        out=out,
        process_group=process.pid,
        start_time=identity[2],
        cleanup_complete=cleanup_complete,
    )
    store = _RunStore()
    state = SimpleNamespace(
        backtest_store=store,
        backtest_cancel_requests=set(),
    )
    backtest._set_backtest_worker_starting("run-owned", True)

    def register_later() -> None:
        time.sleep(0.15)
        backtest._register_backtest_worker("run-owned", worker)
        backtest._set_backtest_worker_starting("run-owned", False)

    registration = threading.Thread(target=register_later, daemon=True)
    registration.start()
    try:
        response = asyncio.run(backtest.run_action("run-owned", "cancel", state))
        assert response["accepted"] is True
        assert response["status"] == "cancelled"
        assert not process.is_alive()
    finally:
        backtest._set_backtest_worker_starting("run-owned", False)
        backtest._unregister_backtest_worker("run-owned", worker)
        assert backtest._terminate_backtest_worker(worker, timeout=2.0) is True
        process.join(timeout=1.0)
        out.close()
        out.cancel_join_thread()
        registration.join(timeout=1.0)


@pytest.mark.asyncio
async def test_backtest_admission_returns_429_when_capacity_is_saturated(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("OPENPINE_BACKTEST_MAX_CONCURRENCY", "1")
    monkeypatch.setattr(backtest, "ws_manager", _Progress())
    strategy = SimpleNamespace(
        strategy_id="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        archived=False,
    )
    store = _RunStore(status="queued")
    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: strategy),
        backtest_store=store,
        backtest_cancel_requests=set(),
    )
    body = SimpleNamespace(
        strategy_id="strategy-1",
        from_time="0",
        to_time="60000",
        warmup_bars=0,
        params_override=None,
        capture_plots=False,
        initial_capital=None,
    )
    background = _BackgroundTasks()

    first = await backtest.run_backtest(body, background, state)
    assert first.status == "queued"

    with pytest.raises(HTTPException) as saturated:
        await backtest.run_backtest(body, background, state)
    assert saturated.value.status_code == 429
    assert len(store.created) == 1

    # The queued task owns the lease until its terminal finally block.
    lease = background.calls[0][1][-1]
    lease.release()


@pytest.mark.asyncio
async def test_backtest_idempotency_reuses_run_and_rejects_key_body_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("OPENPINE_BACKTEST_MAX_CONCURRENCY", "1")
    monkeypatch.setattr(backtest, "ws_manager", _Progress())
    storage = SQLiteStorage(tmp_path / "idempotency.sqlite")
    MigrationRunner().run_migrations(storage)

    strategy = SimpleNamespace(
        strategy_id="strategy-1",
        pine_id="pine-1",
        artifact_id="artifact-1",
        params_hash="params-1",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        timeframe="1m",
        archived=False,
    )

    class Store:
        def __init__(self) -> None:
            self.created: list[object] = []
            self.runs: dict[str, SimpleNamespace] = {}

        def create_run(self, request: object) -> str:
            run_id = f"run-{len(self.created) + 1}"
            self.created.append(request)
            self.runs[run_id] = SimpleNamespace(
                run_id=run_id,
                strategy_id="strategy-1",
                status="queued",
                started_at=123,
            )
            return run_id

        def get_run(self, run_id: str):
            return self.runs.get(run_id)

        def mark_failed(self, run_id: str, message: str) -> None:
            self.runs[run_id].status = "failed"

    store = Store()
    state = SimpleNamespace(
        storage=storage,
        strategy_registry=SimpleNamespace(get_strategy=lambda strategy_id: strategy),
        backtest_store=store,
        backtest_cancel_requests=set(),
    )
    body = SimpleNamespace(
        strategy_id="strategy-1",
        from_time="0",
        to_time="60000",
        warmup_bars=0,
        params_override=None,
        capture_plots=False,
        initial_capital=None,
    )
    background = _BackgroundTasks()
    try:
        first = await backtest.run_backtest(
            body, background, state, idempotency_key="request-key-1"
        )
        repeated = await backtest.run_backtest(
            body, background, state, idempotency_key="request-key-1"
        )
        assert first.run_id == repeated.run_id == "run-1"
        assert len(store.created) == 1
        assert len(background.calls) == 1

        conflicting_payload = vars(body).copy()
        conflicting_payload["initial_capital"] = 1000.0
        conflicting = SimpleNamespace(**conflicting_payload)
        with pytest.raises(HTTPException) as conflict:
            await backtest.run_backtest(
                conflicting,
                background,
                state,
                idempotency_key="request-key-1",
            )
        assert conflict.value.status_code == 409
    finally:
        if background.calls:
            background.calls[0][1][-1].release()
        storage.close()


def test_stale_incomplete_idempotency_claim_can_be_recovered(tmp_path: Path) -> None:
    storage = SQLiteStorage(tmp_path / "stale-idempotency.sqlite")
    MigrationRunner().run_migrations(storage)
    state = cast(GatewayState, SimpleNamespace(storage=storage))
    stale_time = int(time.time() * 1000) - 10 * 60 * 1000
    storage.execute(
        """
        INSERT INTO api_idempotency
            (scope, idempotency_key, request_hash, result_id, created_at, updated_at)
        VALUES (?, ?, ?, NULL, ?, ?)
        """,
        ("backtest.run", "stale-key", "old-hash", stale_time, stale_time),
    )
    storage.commit()

    try:
        recovered = backtest._claim_backtest_idempotency(
            state, "stale-key", "new-hash"
        )
        assert recovered.result_id is None
        assert recovered.claim_token
        row = storage.execute(
            "SELECT request_hash, result_id FROM api_idempotency WHERE scope = ? AND idempotency_key = ?",
            ("backtest.run", "stale-key"),
        ).fetchone()
        assert tuple(row) == ("new-hash", None)
    finally:
        storage.close()


@pytest.mark.parametrize("reclaimed_hash", ["hash-a", "hash-b"])
def test_stale_idempotency_owner_cannot_complete_reclaimed_key(
    tmp_path: Path, reclaimed_hash: str
) -> None:
    storage = SQLiteStorage(tmp_path / "idempotency-owner.sqlite")
    MigrationRunner().run_migrations(storage)
    state = cast(GatewayState, SimpleNamespace(storage=storage))

    try:
        first = backtest._claim_backtest_idempotency(state, "race-key", "hash-a")
        assert first.result_id is None
        assert first.claim_token
        storage.execute(
            "UPDATE api_idempotency SET updated_at = ? WHERE scope = ? AND idempotency_key = ?",
            (
                int(time.time() * 1000) - 10 * 60 * 1000,
                "backtest.run",
                "race-key",
            ),
        )
        storage.commit()

        second = backtest._claim_backtest_idempotency(
            state, "race-key", reclaimed_hash
        )
        assert second.result_id is None
        assert second.claim_token and second.claim_token != first.claim_token

        with pytest.raises(HTTPException) as superseded:
            backtest._complete_backtest_idempotency(
                state,
                "race-key",
                "hash-a",
                first.claim_token,
                "run-a",
            )
        assert superseded.value.status_code == 409

        backtest._release_backtest_idempotency(
            state,
            "race-key",
            "hash-a",
            first.claim_token,
        )
        owned_row = storage.execute(
            "SELECT claim_token FROM api_idempotency WHERE scope = ? AND idempotency_key = ?",
            ("backtest.run", "race-key"),
        ).fetchone()
        assert owned_row[0] == second.claim_token

        backtest._complete_backtest_idempotency(
            state,
            "race-key",
            reclaimed_hash,
            second.claim_token,
            "run-b",
        )
        row = storage.execute(
            "SELECT request_hash, result_id FROM api_idempotency WHERE scope = ? AND idempotency_key = ?",
            ("backtest.run", "race-key"),
        ).fetchone()
        assert tuple(row) == (reclaimed_hash, "run-b")
    finally:
        storage.close()


def test_idempotency_storage_outage_is_service_unavailable() -> None:
    class BrokenStorage:
        def execute(self, *_args, **_kwargs):
            raise RuntimeError("database offline")

    state = cast(GatewayState, SimpleNamespace(storage=BrokenStorage()))

    with pytest.raises(HTTPException) as exc_info:
        backtest._claim_backtest_idempotency(state, "request-key", "request-hash")

    assert exc_info.value.status_code == 503
