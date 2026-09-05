from __future__ import annotations

import asyncio
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest
from fastapi import FastAPI, HTTPException

from openpine.gateway import server
from openpine.gateway.config import GatewayConfig
from openpine.gateway.live_runner import LiveStrategyRunner, RunnerConfig
from openpine.gateway.routes import dashboard, strategies, trading
from openpine.gateway.schemas import LiveStartRequest, PaperStartRequest, StrategyUpdate
from openpine.gateway.worker_supervisor import (
    SupervisorConfig,
    WorkerSupervisor,
    worker_accepts_strategy_activation,
    worker_runtime_snapshot,
)
from openpine.registry.strategies import SQLiteStrategyRegistry, WorkerCircuitOpenError
from tests.admission_helpers import STACK_HASH, make_deployment_identity


class _StopEvent:
    def __init__(self) -> None:
        self.set_called = False

    def set(self) -> None:
        self.set_called = True

    def is_set(self) -> bool:
        return self.set_called


class _Heartbeat:
    def __init__(self) -> None:
        self.value = 0.0


class _Process:
    _next_pid = 7000

    def __init__(self) -> None:
        type(self)._next_pid += 1
        self.pid = type(self)._next_pid
        self.exitcode: int | None = None
        self.alive = False
        self.join_calls: list[float | None] = []
        self.terminate_called = False

    def start(self) -> None:
        self.alive = True

    def is_alive(self) -> bool:
        return self.alive

    def join(self, timeout: float | None = None) -> None:
        self.join_calls.append(timeout)

    def terminate(self) -> None:
        self.terminate_called = True
        self.kill(-15)

    def kill(self, exitcode: int = -9) -> None:
        self.alive = False
        self.exitcode = exitcode


class _Factory:
    def __init__(self) -> None:
        self.processes: list[_Process] = []
        self.events: list[_StopEvent] = []
        self.ready_events: list[_StopEvent] = []
        self.heartbeats: list[_Heartbeat] = []

    def __call__(self) -> tuple[_Process, _StopEvent, _StopEvent, _Heartbeat]:
        process = _Process()
        event = _StopEvent()
        ready_event = _StopEvent()
        heartbeat = _Heartbeat()
        self.processes.append(process)
        self.events.append(event)
        self.ready_events.append(ready_event)
        self.heartbeats.append(heartbeat)
        return process, event, ready_event, heartbeat


async def _eventually(predicate, *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.001)


@pytest.mark.asyncio
async def test_supervisor_reaps_and_restarts_dead_worker_with_runtime_state() -> None:
    factory = _Factory()
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.001,
            backoff_max_seconds=0.002,
            max_restarts=2,
            restart_window_seconds=60.0,
        ),
    )

    supervisor.start()
    first = factory.processes[0]
    first.kill(-9)
    await _eventually(lambda: len(factory.processes) == 2)

    status = supervisor.snapshot()
    assert first.join_calls
    assert status["pid"] == factory.processes[1].pid
    assert status["alive"] is True
    assert status["exitcode"] is None
    assert status["restart_count"] == 1
    assert status["last_transition"] is not None
    assert status["degraded"] is True
    assert status["reason"] == "worker_restarted"

    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_exhausts_restart_budget_and_invokes_fail_safe_once() -> None:
    factory = _Factory()
    fail_safe_calls: list[str] = []
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: fail_safe_calls.append("paused"),
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.001,
            backoff_max_seconds=0.001,
            max_restarts=1,
            restart_window_seconds=60.0,
        ),
    )

    supervisor.start()
    factory.processes[0].kill(-9)
    await _eventually(lambda: len(factory.processes) == 2)
    factory.processes[1].kill(-9)
    await _eventually(
        lambda: supervisor.snapshot()["reason"] == "restart_budget_exhausted"
    )
    await _eventually(lambda: fail_safe_calls == ["paused"])

    status = supervisor.snapshot()
    assert fail_safe_calls == ["paused"]
    assert len(factory.processes) == 2
    assert status["alive"] is False
    assert status["exitcode"] == -9
    assert status["restart_count"] == 1
    assert status["reason"] == "restart_budget_exhausted"

    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_intended_stop_never_restarts_worker() -> None:
    factory = _Factory()
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.001,
            backoff_max_seconds=0.001,
            max_restarts=2,
            restart_window_seconds=60.0,
            shutdown_timeout_seconds=0.001,
            terminate_timeout_seconds=0.001,
        ),
    )

    supervisor.start()
    await supervisor.stop()
    await asyncio.sleep(0.01)

    assert len(factory.processes) == 1
    assert factory.events[0].set_called is True
    assert factory.processes[0].terminate_called is True
    assert supervisor.snapshot()["reason"] == "stopped"


@pytest.mark.asyncio
async def test_supervisor_health_requires_explicit_ready_and_fresh_heartbeat() -> None:
    factory = _Factory()
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=1.0,
            heartbeat_stale_seconds=5.0,
        ),
    )
    supervisor.start()

    starting = supervisor.snapshot()
    assert starting["alive"] is True
    assert starting["ready"] is False
    assert starting["heartbeat_stale"] is True

    factory.ready_events[0].set()
    factory.heartbeats[0].value = time.time()
    ready = supervisor.snapshot()
    assert ready["ready"] is True
    assert ready["heartbeat_age_seconds"] is not None
    assert ready["heartbeat_stale"] is False
    await supervisor.stop()


def test_registry_fail_safe_pauses_and_disables_enabled_strategies_atomically(
    tmp_path: Path,
) -> None:
    registry = SQLiteStrategyRegistry(tmp_path / "strategies.sqlite")
    enabled = registry.create_strategy(
        name="enabled",
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
    )
    untouched = registry.create_strategy(
        name="disabled",
        pine_id="pine-2",
        artifact_id="artifact-2",
        symbol="ETHUSDT",
        timeframe="1m",
    )
    registry.update_status(enabled.strategy_id, "running")
    registry.set_enabled(enabled.strategy_id, True)
    registry.update_status(untouched.strategy_id, "pending")

    changed = registry.pause_all_enabled()

    current_enabled = registry.get_strategy(enabled.strategy_id)
    current_untouched = registry.get_strategy(untouched.strategy_id)
    assert changed == 1
    assert current_enabled.enabled is False
    assert current_enabled.status == "paused"
    assert current_untouched.enabled is False
    assert current_untouched.status == "pending"
    registry.close()


@pytest.mark.asyncio
async def test_strategy_health_uses_only_latest_metadata_in_worker_thread() -> None:
    entered = threading.Event()
    release = threading.Event()

    class MetadataOnlyOrchestrator:
        def latest_bar_time(self, query) -> int:
            entered.set()
            release.wait(timeout=1.0)
            return 999_000

        def load_bars(self, query):
            raise AssertionError("dashboard health must not materialize bars")

        def read_all(self, *args, **kwargs):
            raise AssertionError("dashboard health must not scan storage")

    class Storage:
        def execute(self, sql, params=()):
            return SimpleNamespace(fetchone=lambda: None)

    worker = SimpleNamespace(
        snapshot=lambda: {
            "enabled": True,
            "pid": 123,
            "alive": True,
            "exitcode": None,
            "restart_count": 0,
            "last_transition": 1.0,
            "degraded": False,
            "reason": None,
        }
    )
    state = SimpleNamespace(
        storage=Storage(),
        orchestrator=MetadataOnlyOrchestrator(),
        _fetcher=None,
        _live_runner=None,
        _background_worker_supervisor=worker,
    )
    strategy = SimpleNamespace(
        strategy_id="s1",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        status="running",
        enabled=True,
    )

    task = asyncio.create_task(dashboard._strategy_health_async(cast(Any, state), strategy))
    timer = threading.Timer(0.2, release.set)
    timer.start()
    try:
        await asyncio.sleep(0.02)
        assert entered.is_set()
        assert task.done() is False
    finally:
        release.set()
        timer.cancel()

    result = await asyncio.wait_for(task, timeout=1.0)
    assert result["last_bar_time"] == 999_000
    assert result["data_lag_seconds"] is not None
    assert result["runner_alive"] is True


@pytest.mark.asyncio
async def test_health_and_dashboard_expose_degraded_worker_runtime() -> None:
    worker_status = {
        "enabled": True,
        "pid": 8123,
        "alive": False,
        "exitcode": -9,
        "restart_count": 3,
        "last_transition": 123.5,
        "degraded": True,
        "reason": "restart_budget_exhausted",
    }
    worker = SimpleNamespace(snapshot=lambda: dict(worker_status))
    app = server.create_app(GatewayConfig(api_prefix="/unit-api", cors_origins=["*"]))
    app.state.gateway = SimpleNamespace(_background_worker_supervisor=worker)
    endpoints = {
        getattr(route, "path", None): getattr(route, "endpoint") for route in app.routes
    }

    health = await endpoints["/health"]()

    assert health["status"] == "degraded"
    assert health["version"] == server.__version__
    assert health["runtime"]["background_worker"] == worker_status

    class EmptyStorage:
        def execute(self, sql, params=()):
            text = str(sql)
            if text.startswith("PRAGMA"):
                return SimpleNamespace(fetchall=lambda: [])
            if "FROM backtest_runs" in text:
                return SimpleNamespace(fetchall=lambda: [])
            return SimpleNamespace(fetchone=lambda: None)

    state = SimpleNamespace(
        strategy_registry=SimpleNamespace(list_strategies=lambda: []),
        scheduler=SimpleNamespace(list_jobs=lambda: []),
        storage=EmptyStorage(),
        orchestrator=SimpleNamespace(),
        _fetcher=None,
        _risk_kill_switch=[False],
        _startup_time=0.0,
        _background_worker_supervisor=worker,
    )
    response = await dashboard.dashboard(state=cast(Any, state))
    assert response.runtime_health["background_worker"] == worker_status


@pytest.mark.asyncio
async def test_health_is_degraded_for_live_but_unready_or_stale_worker() -> None:
    worker_status = {
        "enabled": True,
        "pid": 8124,
        "alive": True,
        "ready": False,
        "heartbeat_stale": True,
        "degraded": False,
        "reason": "starting",
    }
    app = server.create_app(GatewayConfig(api_prefix="/unit-api", cors_origins=["*"]))
    app.state.gateway = SimpleNamespace(
        _background_worker_supervisor=SimpleNamespace(snapshot=lambda: dict(worker_status))
    )
    health_endpoint = next(
        route.endpoint for route in app.routes if getattr(route, "path", None) == "/health"
    )

    health = await health_endpoint()

    assert health["status"] == "degraded"


@pytest.mark.asyncio
async def test_supervisor_shutdown_escalates_and_reports_stubborn_worker_truthfully() -> None:
    operations: list[str] = []

    class StubbornProcess(_Process):
        def join(self, timeout: float | None = None) -> None:
            operations.append("join")

        def terminate(self) -> None:
            operations.append("terminate")

        def kill(self, exitcode: int = -9) -> None:
            operations.append("kill")

    process = StubbornProcess()
    supervisor = WorkerSupervisor(
        lambda: (process, _StopEvent(), _StopEvent(), _Heartbeat()),
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=1.0,
            shutdown_timeout_seconds=0.001,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )
    supervisor.start()

    await supervisor.stop()

    assert operations[-4:] == ["terminate", "join", "kill", "join"]
    status = supervisor.snapshot()
    assert status["alive"] is True
    assert status["degraded"] is True
    assert status["reason"] == "shutdown_incomplete"


@pytest.mark.asyncio
async def test_supervisor_contains_factory_callback_status_and_failsafe_exceptions() -> None:
    def broken_factory():
        raise RuntimeError("factory exploded")

    supervisor = WorkerSupervisor(
        broken_factory,
        fail_safe=lambda: (_ for _ in ()).throw(RuntimeError("failsafe")),
    )
    supervisor.start()
    await asyncio.sleep(0)
    assert supervisor.snapshot()["degraded"] is True
    assert supervisor.snapshot()["reason"] == "process_factory_failed"
    await supervisor.stop()

    factory = _Factory()
    callback_supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: None,
        on_process=lambda _process: (_ for _ in ()).throw(RuntimeError("callback")),
    )
    callback_supervisor.start()
    assert callback_supervisor.snapshot()["degraded"] is True
    assert callback_supervisor.snapshot()["reason"] == "process_callback_failed"
    await callback_supervisor.stop()

    class BrokenStatusProcess(_Process):
        def is_alive(self) -> bool:
            raise RuntimeError("status")

    process = BrokenStatusProcess()
    status_supervisor = WorkerSupervisor(
        lambda: (process, _StopEvent(), _StopEvent(), _Heartbeat()),
        fail_safe=lambda: None,
    )
    status_supervisor.start()
    assert status_supervisor.snapshot()["degraded"] is True
    assert status_supervisor.snapshot()["reason"] == "process_status_failed"
    await status_supervisor.stop()


@pytest.mark.asyncio
async def test_start_exception_after_child_becomes_live_is_contained_without_retry() -> None:
    class StartsThenRaises(_Process):
        def start(self) -> None:
            self.alive = True
            raise RuntimeError("raised after child became live")

    processes: list[_Process] = []

    def factory() -> tuple[_Process, _StopEvent, _StopEvent, _Heartbeat]:
        process: _Process = StartsThenRaises() if not processes else _Process()
        processes.append(process)
        return process, _StopEvent(), _StopEvent(), _Heartbeat()

    fail_safe_calls: list[str] = []
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: fail_safe_calls.append("paused"),
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.001,
            backoff_max_seconds=0.001,
            max_restarts=2,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )

    supervisor.start()
    await _eventually(
        lambda: bool(processes)
        and processes[0].is_alive() is False
        and fail_safe_calls == ["paused"]
    )

    assert len(processes) == 1
    assert processes[0].terminate_called is True
    assert processes[0].is_alive() is False
    assert fail_safe_calls == ["paused"]
    assert supervisor.snapshot()["reason"] == "process_start_failed"

    assert await supervisor.stop() is True
    assert not any(process.is_alive() for process in processes)


@pytest.mark.asyncio
async def test_live_runner_wait_stopped_bounds_task_cancellation() -> None:
    entered = asyncio.Event()
    release = asyncio.Event()
    runner = LiveStrategyRunner(
        config=RunnerConfig(shutdown_timeout_seconds=0.01),
        state_store=None,
    )

    async def stubborn_task() -> None:
        entered.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            await release.wait()

    runner._task = asyncio.create_task(stubborn_task())
    runner._running = True
    await entered.wait()

    runner.stop()
    assert await asyncio.wait_for(runner.wait_stopped(), timeout=0.1) is False
    release.set()
    assert await runner.wait_stopped() is True


@pytest.mark.asyncio
async def test_lifespan_does_not_close_state_when_supervisor_stop_raises(monkeypatch) -> None:
    closed: list[bool] = []

    class Storage:
        def execute(self, *_args, **_kwargs):
            return SimpleNamespace(fetchall=lambda: [])

    class Registry:
        def reset_worker_circuit(self) -> None:
            return None

        def pause_all_enabled(self) -> int:
            return 0

    state = SimpleNamespace(
        config=SimpleNamespace(live_enabled=False, sqlite_path=Path("unused.sqlite")),
        storage=Storage(),
        strategy_registry=Registry(),
        strategy_activation_lock=threading.RLock(),
        close=lambda: closed.append(True),
    )

    class FailingSupervisor:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start(self) -> None:
            return None

        async def stop(self) -> bool:
            raise RuntimeError("supervisor stop failed")

    monkeypatch.setattr(server, "GatewayState", lambda: state)
    monkeypatch.setattr(server, "WorkerSupervisor", FailingSupervisor)
    monkeypatch.setenv("OPENPINE_ENABLE_BACKGROUND_WORKER", "1")
    monkeypatch.setenv("OPENPINE_ENABLE_PERIODIC_FETCHER", "0")
    monkeypatch.setenv("OPENPINE_ENABLE_LIVE_RUNNER", "0")

    with pytest.raises(RuntimeError, match="supervisor stop failed"):
        async with server.lifespan(FastAPI()):
            pass

    assert closed == []


@pytest.mark.asyncio
async def test_lifespan_cleans_started_supervisor_when_fetcher_startup_fails(
    monkeypatch,
) -> None:
    calls: list[str] = []

    class FailingFetcher:
        def __init__(self, *_args, **_kwargs) -> None:
            raise RuntimeError("fetcher startup failed")

    class Supervisor:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start(self) -> None:
            calls.append("supervisor-start")

        async def stop(self) -> bool:
            calls.append("supervisor-stop")
            return True

    state = SimpleNamespace(
        config=SimpleNamespace(live_enabled=False, sqlite_path=Path("unused.sqlite")),
        storage=SimpleNamespace(
            execute=lambda *_args, **_kwargs: SimpleNamespace(fetchall=lambda: [])
        ),
        strategy_registry=SimpleNamespace(
            reset_worker_circuit=lambda: None,
            pause_enabled_strategies=lambda: 0,
        ),
        strategy_activation_lock=threading.RLock(),
        orchestrator=SimpleNamespace(),
        artifact_store=SimpleNamespace(),
        close=lambda: calls.append("state-close"),
    )
    monkeypatch.setattr(server, "GatewayState", lambda: state)
    monkeypatch.setattr(server, "WorkerSupervisor", Supervisor)
    monkeypatch.setattr(
        "openpine.data.periodic_fetcher.PeriodicBarFetcher",
        FailingFetcher,
    )
    monkeypatch.setenv("OPENPINE_ENABLE_BACKGROUND_WORKER", "1")
    monkeypatch.setenv("OPENPINE_ENABLE_PERIODIC_FETCHER", "1")
    monkeypatch.setenv("OPENPINE_ENABLE_LIVE_RUNNER", "0")

    with pytest.raises(RuntimeError, match="fetcher startup failed"):
        async with server.lifespan(FastAPI()):
            pass

    assert calls == ["supervisor-start", "supervisor-stop", "state-close"]


@pytest.mark.asyncio
async def test_lifespan_stops_fetcher_when_live_runner_shutdown_raises(monkeypatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr("openpine.live_release_gate.LIVE_RELEASE_ENABLED", True)

    class Storage:
        def execute(self, *_args, **_kwargs):
            return SimpleNamespace(fetchall=lambda: [])

    class Runner:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start(self) -> None:
            calls.append("runner-start")

        def stop(self) -> None:
            calls.append("runner-stop")

        async def wait_stopped(self) -> bool:
            calls.append("runner-wait")
            raise RuntimeError("runner shutdown failed")

    class Fetcher:
        def __init__(self, *_args, **_kwargs) -> None:
            return None

        def start(self) -> None:
            calls.append("fetcher-start")

        def stop(self) -> None:
            calls.append("fetcher-stop")

    state = SimpleNamespace(
        config=SimpleNamespace(live_enabled=True, sqlite_path=Path("unused.sqlite")),
        storage=Storage(),
        strategy_registry=SimpleNamespace(),
        orchestrator=SimpleNamespace(),
        artifact_store=SimpleNamespace(),
        close=lambda: calls.append("state-close"),
    )
    monkeypatch.setattr(server, "GatewayState", lambda: state)
    monkeypatch.setattr(
        "openpine.gateway.live_runner.LiveStrategyRunner",
        Runner,
    )
    monkeypatch.setattr(
        "openpine.data.periodic_fetcher.PeriodicBarFetcher",
        Fetcher,
    )
    monkeypatch.setenv("OPENPINE_ENABLE_BACKGROUND_WORKER", "0")
    monkeypatch.setenv("OPENPINE_ENABLE_PERIODIC_FETCHER", "1")
    monkeypatch.setenv("OPENPINE_ENABLE_LIVE_RUNNER", "1")

    with pytest.raises(RuntimeError, match="runner shutdown failed"):
        async with server.lifespan(FastAPI()):
            pass

    assert calls == [
        "fetcher-start",
        "runner-start",
        "runner-stop",
        "runner-wait",
        "fetcher-stop",
    ]


@pytest.mark.asyncio
async def test_lifespan_always_closes_state_when_application_body_raises(monkeypatch) -> None:
    closed: list[bool] = []

    class Storage:
        def execute(self, *_args, **_kwargs):
            return SimpleNamespace(fetchall=lambda: [])

    state = SimpleNamespace(
        config=SimpleNamespace(live_enabled=False, sqlite_path=Path("unused.sqlite")),
        storage=Storage(),
        strategy_registry=SimpleNamespace(pause_all_enabled=lambda: 0),
        close=lambda: closed.append(True),
    )
    monkeypatch.setattr(server, "GatewayState", lambda: state)
    monkeypatch.setenv("OPENPINE_ENABLE_BACKGROUND_WORKER", "0")
    monkeypatch.delenv("OPENPINE_ENABLE_PERIODIC_FETCHER", raising=False)
    monkeypatch.delenv("OPENPINE_ENABLE_LIVE_RUNNER", raising=False)

    with pytest.raises(RuntimeError, match="application failed"):
        async with server.lifespan(FastAPI()):
            raise RuntimeError("application failed")

    assert closed == [True]


@pytest.mark.asyncio
async def test_dashboard_metadata_failure_is_explicitly_degraded() -> None:
    class BrokenMetadata:
        def latest_bar_time(self, _query):
            raise OSError("manifest unavailable")

    state = SimpleNamespace(
        storage=SimpleNamespace(execute=lambda *_args, **_kwargs: SimpleNamespace(fetchone=lambda: None)),
        orchestrator=BrokenMetadata(),
        _fetcher=None,
        _live_runner=None,
        _background_worker_supervisor=None,
        _background_worker_process=None,
    )
    strategy = SimpleNamespace(
        strategy_id="s1",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        status="running",
        enabled=False,
    )

    health = await dashboard._strategy_health_async(cast(Any, state), strategy)

    assert health["status"] == "metadata_error"
    assert health["metadata_ok"] is False


@pytest.mark.asyncio
async def test_strategy_start_and_enable_are_blocked_while_worker_is_unready() -> None:
    calls: list[tuple[str, object]] = []
    strategy = SimpleNamespace(strategy_id="s1", archived=False, status="paused", mode="paper", semantic_profile="strict_5x")

    class Registry:
        def get_strategy(self, _strategy_id):
            return strategy

        def update_status(self, strategy_id, status):
            calls.append((strategy_id, status))

        def set_enabled(self, strategy_id, enabled):
            calls.append((strategy_id, enabled))

    worker = SimpleNamespace(
        snapshot=lambda: {
            "enabled": True,
            "alive": True,
            "ready": False,
            "heartbeat_stale": True,
            "degraded": False,
            "reason": "starting",
        }
    )
    state = SimpleNamespace(
        strategy_registry=Registry(),
        strategy_activation_lock=threading.RLock(),
        _background_worker_supervisor=worker,
    )

    with pytest.raises(HTTPException) as action_error:
        await strategies.strategy_action("s1", state=cast(Any, state), action="start")
    assert action_error.value.status_code == 503
    with pytest.raises(HTTPException) as enable_error:
        await strategies.strategy_enable("s1", state=cast(Any, state))
    assert enable_error.value.status_code == 503
    assert calls == []


@pytest.mark.asyncio
async def test_strategy_patch_enabled_true_cannot_bypass_worker_guard() -> None:
    calls: list[tuple[str, object]] = []
    strategy = SimpleNamespace(strategy_id="s1", archived=False, enabled=False, status="paused", mode="paper", semantic_profile="strict_5x")

    class Registry:
        def get_strategy(self, _strategy_id):
            return strategy

        def set_enabled(self, strategy_id, enabled):
            calls.append((strategy_id, enabled))

    state = SimpleNamespace(
        strategy_registry=Registry(),
        strategy_activation_lock=threading.RLock(),
        _background_worker_supervisor=SimpleNamespace(
            snapshot=lambda: {
                "enabled": True,
                "alive": True,
                "ready": False,
                "heartbeat_stale": True,
                "degraded": False,
                "reason": "heartbeat_stale",
            }
        ),
    )

    with pytest.raises(HTTPException) as error:
        await strategies.update_strategy(
            "s1", StrategyUpdate(enabled=True), state=cast(Any, state)
        )

    assert error.value.status_code == 503
    assert calls == []


def test_durable_worker_circuit_rejects_enable_after_fail_safe(tmp_path: Path) -> None:
    registry = SQLiteStrategyRegistry(tmp_path / "strategies.sqlite")
    strategy = registry.create_strategy(
        name="guarded",
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
    )

    registry.trip_worker_circuit("restart_budget_exhausted")

    with pytest.raises(WorkerCircuitOpenError):
        registry.set_enabled(strategy.strategy_id, True)
    current = registry.get_strategy(strategy.strategy_id)
    assert current.enabled is False
    assert registry.worker_circuit_state()["open"] is True
    registry.close()


def test_enable_race_cannot_win_after_worker_fail_safe(tmp_path: Path, monkeypatch) -> None:
    registry = SQLiteStrategyRegistry(tmp_path / "strategies.sqlite")
    strategy = registry.create_strategy(
        name="race",
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
        semantic_profile="strict_5x",
    )
    guard_passed = threading.Event()
    release_guard = threading.Event()
    result: list[BaseException | None] = []

    @contextmanager
    def paused_guard(_state):
        guard_passed.set()
        release_guard.wait(timeout=2.0)
        yield

    monkeypatch.setattr(strategies, "guarded_strategy_activation", paused_guard)
    state = SimpleNamespace(strategy_registry=registry)

    def invoke() -> None:
        try:
            asyncio.run(
                strategies.strategy_action(
                    strategy.strategy_id, state=cast(Any, state), action="start"
                )
            )
        except BaseException as exc:  # test thread hand-off
            result.append(exc)
        else:
            result.append(None)

    thread = threading.Thread(target=invoke)
    thread.start()
    assert guard_passed.wait(timeout=1.0)
    registry.trip_worker_circuit("restart_budget_exhausted")
    release_guard.set()
    thread.join(timeout=2.0)

    assert isinstance(result[0], HTTPException)
    assert cast(HTTPException, result[0]).status_code == 503
    assert registry.get_strategy(strategy.strategy_id).enabled is False
    registry.close()


@pytest.mark.asyncio
async def test_fail_safe_failure_is_retried_and_exposed_in_health() -> None:
    attempts = 0

    def flaky_fail_safe() -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("database is locked")

    supervisor = WorkerSupervisor(
        lambda: (_ for _ in ()).throw(RuntimeError("cannot start")),
        fail_safe=flaky_fail_safe,
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
            max_restarts=2,
            fail_safe_max_attempts=2,
            fail_safe_retry_seconds=0.0,
        ),
    )
    supervisor.start()
    await _eventually(lambda: supervisor.snapshot()["fail_safe_status"] == "ok")

    status = supervisor.snapshot()
    assert attempts == 2
    assert status["fail_safe_attempts"] == 2
    assert status["fail_safe_error"] is None
    await supervisor.stop()


@pytest.mark.asyncio
async def test_stale_heartbeat_terminates_and_restarts_live_hung_worker() -> None:
    factory = _Factory()
    fail_safe_calls: list[str] = []
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: fail_safe_calls.append("tripped"),
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
            heartbeat_stale_seconds=1.0,
            shutdown_timeout_seconds=0.001,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )
    supervisor.start()
    factory.ready_events[0].set()
    factory.heartbeats[0].value = time.time() - 10.0

    await _eventually(lambda: bool(fail_safe_calls))
    await _eventually(lambda: not factory.processes[0].is_alive())
    await _eventually(lambda: len(factory.processes) == 2)
    factory.ready_events[1].set()
    factory.heartbeats[1].value = time.time()
    await _eventually(lambda: supervisor.snapshot()["ready"] is True)

    status = supervisor.snapshot()
    assert factory.processes[0].terminate_called is True
    assert status["alive"] is True
    assert status["restart_count"] == 1
    assert status["degraded"] is True
    assert status["reason"] == "heartbeat_stale_restarted"

    factory.ready_events[1].set()
    factory.heartbeats[1].value = time.time()
    await asyncio.sleep(0.01)

    recovered = supervisor.snapshot()
    assert recovered["ready"] is True
    assert recovered["heartbeat_stale"] is False
    assert recovered["degraded"] is False
    assert recovered["reason"] == "recovered"
    await supervisor.stop()


@pytest.mark.asyncio
async def test_stale_heartbeat_never_starts_replacement_while_old_worker_survives() -> None:
    factory = _Factory()
    fail_safe_calls: list[str] = []
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: fail_safe_calls.append("tripped"),
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            heartbeat_stale_seconds=0.001,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )
    supervisor.start()
    process = factory.processes[0]
    process.terminate = lambda: None
    process.kill = lambda exitcode=-9: None
    factory.ready_events[0].set()
    factory.heartbeats[0].value = time.time() - 10.0

    await _eventually(
        lambda: supervisor.snapshot()["reason"]
        == "unhealthy_process_shutdown_incomplete"
    )

    status = supervisor.snapshot()
    assert fail_safe_calls == ["tripped"]
    assert len(factory.processes) == 1
    assert status["alive"] is True
    assert status["restart_count"] == 0
    assert status["degraded"] is True

    process.alive = False
    await supervisor.stop()


@pytest.mark.asyncio
async def test_unexpected_monitor_failure_never_leaves_unmonitored_worker_alive(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    factory = _Factory()
    fail_safe_calls: list[str] = []
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: fail_safe_calls.append("monitor_failed"),
        config=SupervisorConfig(
            poll_interval_seconds=0.01,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
            max_restarts=1,
            restart_window_seconds=60.0,
            terminate_timeout_seconds=0.01,
            kill_timeout_seconds=0.01,
        ),
    )

    supervisor.start()
    process = factory.processes[0]
    original_safe_is_alive = supervisor._safe_is_alive
    monitor_failed = asyncio.Event()

    def fail_once(_process: object) -> bool:
        monitor_failed.set()
        raise RuntimeError("monitor exploded")

    monkeypatch.setattr(supervisor, "_safe_is_alive", fail_once)
    await monitor_failed.wait()
    monkeypatch.setattr(supervisor, "_safe_is_alive", original_safe_is_alive)
    await _eventually(lambda: supervisor.snapshot()["reason"] == "monitor_failed")
    await _eventually(lambda: fail_safe_calls == ["monitor_failed"])
    await _eventually(lambda: process.terminate_called)

    assert fail_safe_calls == ["monitor_failed"]
    assert process.terminate_called is True
    assert process.alive is False
    await supervisor.stop()


@pytest.mark.asyncio
async def test_supervisor_retries_process_start_failure_within_restart_budget() -> None:
    factory = _Factory()
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
            max_restarts=3,
            restart_window_seconds=60.0,
        ),
    )
    original_factory = supervisor._process_factory
    failed_once = False

    def factory_with_one_start_failure():
        nonlocal failed_once
        created = original_factory()
        if len(factory.processes) == 2 and not failed_once:
            failed_once = True
            created[0].start = lambda: (_ for _ in ()).throw(RuntimeError("start failed"))
        return created

    supervisor._process_factory = factory_with_one_start_failure
    supervisor.start()
    factory.processes[0].kill(-9)

    await _eventually(lambda: len(factory.processes) == 3)
    await _eventually(lambda: factory.processes[-1].is_alive())

    status = supervisor.snapshot()
    assert status["alive"] is True
    assert status["restart_count"] == 2
    assert status["degraded"] is True
    assert status["reason"] == "worker_restarted"
    await supervisor.stop()


@pytest.mark.asyncio
async def test_stop_reports_unsafe_close_while_fail_safe_thread_is_still_running() -> None:
    entered = threading.Event()
    release = threading.Event()

    def blocking_fail_safe() -> None:
        entered.set()
        release.wait(timeout=2.0)

    supervisor = WorkerSupervisor(
        lambda: (_ for _ in ()).throw(RuntimeError("cannot start")),
        fail_safe=blocking_fail_safe,
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
            max_restarts=0,
            shutdown_timeout_seconds=0.01,
            fail_safe_max_attempts=1,
        ),
    )
    supervisor.start()
    assert await asyncio.to_thread(entered.wait, 1.0)

    safe_to_close = await supervisor.stop()

    assert safe_to_close is False
    assert supervisor.snapshot()["reason"] == "fail_safe_shutdown_incomplete"
    release.set()
    await _eventually(lambda: supervisor.fail_safe_running is False)
    assert await supervisor.stop() is True


def test_registry_activation_updates_mode_status_and_enabled_atomically(tmp_path: Path) -> None:
    registry = SQLiteStrategyRegistry(tmp_path / "strategies.sqlite")
    strategy = registry.create_strategy(
        name="atomic",
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
    )

    registry.activate_strategy(strategy.strategy_id, status="running", mode="paper")

    current = registry.get_strategy(strategy.strategy_id)
    assert current.enabled is True
    assert current.status == "running"
    assert current.mode == "paper"
    registry.close()


def test_registry_patch_rolls_back_every_field_on_database_failure(tmp_path: Path) -> None:
    registry = SQLiteStrategyRegistry(tmp_path / "strategies.sqlite")
    strategy = registry.create_strategy(
        name="before",
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
    )
    registry._conn.execute(
        """CREATE TRIGGER reject_live_patch BEFORE UPDATE ON strategy_instances
           WHEN NEW.mode = 'live'
           BEGIN SELECT RAISE(ABORT, 'reject live'); END"""
    )
    registry._conn.commit()

    with pytest.raises(sqlite3.IntegrityError, match="reject live"):
        registry.patch_strategy_atomic(
            strategy.strategy_id,
            {"name": "after", "mode": "live", "enabled": True},
        )

    current = registry.get_strategy(strategy.strategy_id)
    assert current.name == "before"
    assert current.mode == "paper"
    assert current.enabled is False
    registry.close()


@pytest.mark.asyncio
async def test_paper_start_uses_single_atomic_registry_activation() -> None:
    calls: list[tuple[str, str | None, str | None]] = []
    events: list[str] = []
    strategy = SimpleNamespace(
        strategy_id="s1",
        status="paused",
        semantic_profile="strict_5x",
    )

    class Registry:
        def get_strategy(self, _strategy_id):
            return strategy

        def activate_strategy(self, strategy_id, *, status=None, mode=None):
            events.append("activate")
            calls.append((strategy_id, status, mode))

        def set_mtf_series(self, strategy_id, series):
            events.append("persist")

        def update_status(self, *_args):
            raise AssertionError("non-atomic status update")

        def update_mode(self, *_args):
            raise AssertionError("non-atomic mode update")

        def set_enabled(self, *_args):
            raise AssertionError("non-atomic enable update")

    state = SimpleNamespace(
        strategy_registry=Registry(),
        strategy_activation_lock=threading.RLock(),
        _background_worker_supervisor=None,
        admission_identity=make_deployment_identity(),
    )

    result = await trading.start_paper(
        PaperStartRequest(strategy_id="s1"), state=cast(Any, state)
    )

    assert result.status == "running"
    assert events == ["persist", "activate"]
    assert calls == [("s1", "running", "paper")]


def test_legacy_unsupervised_worker_fails_closed_without_child_health_evidence() -> None:
    process = _Process()
    process.start()
    state = SimpleNamespace(
        _background_worker_supervisor=None,
        _background_worker_process=process,
    )

    status = worker_runtime_snapshot(state)
    allowed, activation_status = worker_accepts_strategy_activation(state)

    assert status["alive"] is True
    assert status["ready"] is False
    assert status["heartbeat_stale"] is True
    assert status["degraded"] is True
    assert status["reason"] == "legacy_unsupervised"
    assert allowed is False
    assert activation_status == status


@pytest.mark.asyncio
async def test_dedicated_enable_translates_late_circuit_race_to_http_503() -> None:
    strategy = SimpleNamespace(strategy_id="s1", archived=False, status="paused", mode="paper", semantic_profile="strict_5x")

    class Registry:
        def get_strategy(self, _strategy_id):
            return strategy

        def activate_strategy(self, *_args, **_kwargs):
            raise WorkerCircuitOpenError("Background worker circuit is open")

        def set_enabled(self, *_args):
            raise AssertionError("dedicated enable must use common atomic activation")

    state = SimpleNamespace(
        strategy_registry=Registry(),
        strategy_activation_lock=threading.RLock(),
        _background_worker_supervisor=SimpleNamespace(
            snapshot=lambda: {
                "enabled": True,
                "alive": True,
                "ready": True,
                "heartbeat_stale": False,
                "degraded": False,
                "reason": None,
            }
        ),
    )

    with pytest.raises(HTTPException) as error:
        await strategies.strategy_enable(
            "s1", registry=cast(Any, state.strategy_registry), state=cast(Any, state)
        )

    assert error.value.status_code == 503


@pytest.mark.asyncio
async def test_every_activation_surface_holds_the_shared_lock_for_guard_and_write(
    monkeypatch,
) -> None:
    monkeypatch.setattr("openpine.live_release_gate.LIVE_RELEASE_ENABLED", True)
    lock = threading.RLock()
    strategy = SimpleNamespace(
        strategy_id="s1",
        name="guarded",
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
        exchange="binance",
        market_type="spot",
        params_json="{}",
        params_hash="hash",
        mode="paper",
        semantic_profile="strict_5x",
        enabled=False,
        archived=False,
        status="paused",
        created_at=1,
        updated_at=1,
    )

    def assert_locked() -> None:
        assert lock._is_owned()  # type: ignore[attr-defined]

    class Worker:
        def snapshot(self):
            assert_locked()
            return {
                "enabled": True,
                "alive": True,
                "ready": True,
                "heartbeat_stale": False,
                "degraded": False,
                "reason": None,
            }

    class Registry:
        def get_strategy(self, _strategy_id):
            return strategy

        def activate_strategy(self, _strategy_id, *, status=None, mode=None):
            assert_locked()
            strategy.enabled = True
            if status is not None:
                strategy.status = status

        def patch_strategy_atomic(self, _strategy_id, updates):
            assert_locked()
            strategy.enabled = bool(updates.get("enabled", strategy.enabled))

    registry = Registry()
    state = SimpleNamespace(
        strategy_registry=registry,
        strategy_activation_lock=lock,
        _background_worker_supervisor=Worker(),
        config=SimpleNamespace(live_enabled=True),
        admission_identity=make_deployment_identity(),
    )

    await strategies.strategy_enable(
        "s1", registry=cast(Any, registry), state=cast(Any, state)
    )
    strategy.enabled = False
    await strategies.update_strategy(
        "s1", StrategyUpdate(enabled=True), state=cast(Any, state)
    )
    strategy.enabled = False
    await strategies.strategy_action("s1", state=cast(Any, state), action="start")
    strategy.enabled = False
    await trading.start_paper(
        PaperStartRequest(strategy_id="s1", semantic_profile="strict_5x"), state=cast(Any, state)
    )
    strategy.enabled = False
    from openpine.live_preview import make_live_preview
    import time

    preview = make_live_preview(
        "s1", now_ms=int(time.time() * 1000), stack_id=STACK_HASH
    )
    await trading.start_live(
        trading.LiveStartRequest(
            strategy_id="s1",
            preview_hash=preview["preview_hash"],
            confirmation="LIVE",
            idempotency_key="live-s1",
            expires_at_utc_ms=preview["expires_at_utc_ms"],
            semantic_profile="strict_5x",
        ),
        state=cast(Any, state),
    )


def test_registry_serializes_every_shared_connection_operation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import openpine.registry.strategies as registry_mod

    real_connect = sqlite3.connect
    real_rlock = threading.RLock
    locks = []

    class TrackingRLock:
        def __init__(self) -> None:
            self.inner = real_rlock()

        def __enter__(self):
            self.inner.acquire()
            return self

        def __exit__(self, *_args):
            self.inner.release()

        def owned(self) -> bool:
            return self.inner._is_owned()  # type: ignore[attr-defined]

    class CheckedConnection:
        def __init__(self, inner) -> None:
            self.inner = inner

        def _check(self) -> None:
            assert locks and locks[0].owned(), "SQLite connection used outside registry RLock"

        def execute(self, *args, **kwargs):
            self._check()
            return self.inner.execute(*args, **kwargs)

        def commit(self):
            self._check()
            return self.inner.commit()

        def rollback(self):
            self._check()
            return self.inner.rollback()

        def close(self):
            self._check()
            return self.inner.close()

    def make_lock():
        lock = TrackingRLock()
        locks.append(lock)
        return lock

    def connect(*args, **kwargs):
        return CheckedConnection(real_connect(*args, **kwargs))

    monkeypatch.setattr(registry_mod.threading, "RLock", make_lock)
    monkeypatch.setattr(registry_mod.sqlite3, "connect", connect)
    registry = SQLiteStrategyRegistry(tmp_path / "locked.sqlite")
    strategy = registry.create_strategy(
        name="locked",
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
    )

    registry.get_strategy(strategy.strategy_id)
    registry.list_strategies()
    registry.update_status(strategy.strategy_id, "paused")
    registry.set_enabled(strategy.strategy_id, True)
    registry.activate_strategy(strategy.strategy_id, status="running", mode="paper")
    registry.transition_strategy(strategy.strategy_id, enabled=False, status="paused")
    registry.patch_strategy_atomic(strategy.strategy_id, {"mode": "paper"})
    registry.worker_circuit_state()
    registry.trip_worker_circuit("test")
    registry.reset_worker_circuit()
    registry.pause_all_enabled()
    registry.set_archived(strategy.strategy_id, True)
    registry.set_archived(strategy.strategy_id, False)
    registry.update_mode(strategy.strategy_id, "paper")
    registry.delete_strategy(strategy.strategy_id)
    registry.close()


@pytest.mark.asyncio
async def test_unknown_liveness_escalates_and_never_reports_stopped() -> None:
    operations = []

    class UnknownProcess(_Process):
        def is_alive(self):
            operations.append("probe")
            raise OSError("status unavailable")

        def terminate(self):
            operations.append("terminate")

        def kill(self, exitcode=-9):
            operations.append("kill")

    process = UnknownProcess()
    supervisor = WorkerSupervisor(
        lambda: (process, _StopEvent(), _StopEvent(), _Heartbeat()),
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=1.0,
            shutdown_timeout_seconds=0.001,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )
    supervisor.start()

    safe_to_close = await supervisor.stop()
    status = supervisor.snapshot()

    assert operations.count("probe") >= 3
    assert "terminate" in operations
    assert "kill" in operations
    assert safe_to_close is False
    assert status["liveness"] == "unknown"
    assert status["alive"] is None
    assert status["degraded"] is True
    assert status["reason"] != "stopped"


@pytest.mark.asyncio
async def test_unknown_liveness_never_authorizes_replacement() -> None:
    factory = _Factory()
    fail_safe_calls = []
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: fail_safe_calls.append("failed-closed"),
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )
    supervisor.start()
    process = factory.processes[0]
    process.is_alive = lambda: (_ for _ in ()).throw(OSError("status unavailable"))

    await _eventually(lambda: process.terminate_called)
    await asyncio.sleep(0.01)

    status = supervisor.snapshot()
    assert fail_safe_calls == ["failed-closed"]
    assert len(factory.processes) == 1
    assert status["restart_count"] == 0
    assert status["liveness"] == "unknown"
    assert status["degraded"] is True
    await supervisor.stop()


@pytest.mark.asyncio
async def test_normal_replacement_stays_degraded_until_ready_and_fresh() -> None:
    factory = _Factory()
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
            heartbeat_stale_seconds=1.0,
        ),
    )
    supervisor.start()
    factory.processes[0].kill(-9)
    await _eventually(lambda: len(factory.processes) == 2)

    replacement = supervisor.snapshot()
    assert replacement["ready"] is False
    assert replacement["degraded"] is True
    assert replacement["reason"] == "worker_restarted"

    factory.ready_events[1].set()
    factory.heartbeats[1].value = time.time()
    await _eventually(lambda: supervisor.snapshot()["reason"] == "recovered")
    recovered = supervisor.snapshot()
    assert recovered["ready"] is True
    assert recovered["heartbeat_stale"] is False
    assert recovered["degraded"] is False
    await supervisor.stop()


@pytest.mark.asyncio
async def test_startup_readiness_timeout_fails_safe_and_replaces_hung_child() -> None:
    factory = _Factory()
    fail_safe_calls: list[str] = []
    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: fail_safe_calls.append("tripped"),
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
            max_restarts=1,
            startup_readiness_timeout_seconds=0.005,
            fail_safe_attempt_timeout_seconds=0.01,
            shutdown_timeout_seconds=0.01,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )
    supervisor.start()

    await _eventually(lambda: bool(fail_safe_calls))
    await _eventually(lambda: factory.processes[0].terminate_called)
    await _eventually(lambda: len(factory.processes) == 2)

    assert factory.processes[0].is_alive() is False
    assert supervisor.snapshot()["reason"] == "startup_readiness_timeout_restarted"
    await supervisor.stop()


@pytest.mark.asyncio
async def test_blocked_fail_safe_does_not_block_stale_child_termination() -> None:
    factory = _Factory()
    entered = threading.Event()
    release = threading.Event()

    def blocking_fail_safe() -> None:
        entered.set()
        release.wait(timeout=2.0)

    supervisor = WorkerSupervisor(
        factory,
        fail_safe=blocking_fail_safe,
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
            max_restarts=1,
            heartbeat_stale_seconds=0.001,
            fail_safe_attempt_timeout_seconds=0.005,
            shutdown_timeout_seconds=0.01,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )
    supervisor.start()
    factory.ready_events[0].set()
    factory.heartbeats[0].value = time.time() - 10.0

    assert await asyncio.to_thread(entered.wait, 1.0)
    await _eventually(lambda: factory.processes[0].terminate_called)
    assert supervisor.fail_safe_running is True
    await asyncio.sleep(0.01)
    assert len(factory.processes) == 1

    release.set()
    await _eventually(lambda: supervisor.fail_safe_running is False)
    await _eventually(lambda: len(factory.processes) == 2)
    await supervisor.stop()


@pytest.mark.asyncio
async def test_legacy_two_value_worker_never_fabricates_readiness() -> None:
    process = _Process()
    supervisor = WorkerSupervisor(
        lambda: (process, _StopEvent()),
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            startup_readiness_timeout_seconds=1.0,
        ),
    )
    supervisor.start()

    status = supervisor.snapshot()
    assert status["alive"] is True
    assert status["ready"] is False
    assert status["heartbeat_stale"] is True
    assert status["degraded"] is True
    assert status["reason"] == "worker_starting"
    await supervisor.stop()


def test_registry_rejects_activation_of_archived_strategy_atomically(
    tmp_path: Path,
) -> None:
    registry = SQLiteStrategyRegistry(tmp_path / "archived.sqlite")
    strategy = registry.create_strategy(
        name="archived",
        pine_id="pine-1",
        artifact_id="artifact-1",
        symbol="BTCUSDT",
        timeframe="1m",
    )
    registry.set_archived(strategy.strategy_id, True)

    with pytest.raises(RuntimeError, match="[Aa]rchived"):
        registry.activate_strategy(strategy.strategy_id, status="running", mode="paper")

    current = registry.get_strategy(strategy.strategy_id)
    assert current.archived is True
    assert current.enabled is False
    assert current.status != "running"
    registry.close()


@pytest.mark.asyncio
async def test_trading_start_routes_reject_archived_strategy(monkeypatch) -> None:
    import time

    from openpine.live_preview import make_live_preview

    monkeypatch.setattr("openpine.live_release_gate.LIVE_RELEASE_ENABLED", True)
    strategy = SimpleNamespace(strategy_id="archived", status="paused", archived=True)

    class Registry:
        def get_strategy(self, _strategy_id):
            return strategy

        def activate_strategy(self, *_args, **_kwargs):
            raise AssertionError("archived strategy reached activation")

    state = SimpleNamespace(
        strategy_registry=Registry(),
        config=SimpleNamespace(live_enabled=True),
        admission_identity=make_deployment_identity(),
    )

    with pytest.raises(HTTPException) as paper_exc:
        await trading.start_paper(PaperStartRequest(strategy_id="archived", semantic_profile="strict_5x"), state)
    assert paper_exc.value.status_code == 400

    preview = make_live_preview(
        "archived", now_ms=int(time.time() * 1000), stack_id=STACK_HASH
    )
    with pytest.raises(HTTPException) as live_exc:
        await trading.start_live(
            LiveStartRequest(
                strategy_id="archived",
                preview_hash=preview["preview_hash"],
                confirmation="LIVE",
                idempotency_key="live-archived",
                expires_at_utc_ms=preview["expires_at_utc_ms"],
                semantic_profile="strict_5x",
            ),
            state,
        )
    assert live_exc.value.status_code == 400
