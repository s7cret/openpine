from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from marketdata_provider.contracts import Bar, BarQuery, BarSeries, InstrumentKey, parse_timeframe

from openpine.data.orchestrator import DataOrchestrator
from openpine.gateway.routes import accounts_data
from openpine.gateway import server
from openpine.gateway.worker_supervisor import (
    SupervisorConfig,
    WorkerSupervisor,
    worker_runtime_snapshot,
)
from openpine.jobs import Job, JobScheduler, JobStatus, JobType
from openpine.jobs.persist import JobV1Store


class _SqliteBarOrchestrator:
    """Small SQLite-backed provider/store used to exercise backfill aggregation."""

    def __init__(self, path: Path, bars: tuple[Bar, ...]) -> None:
        self._source_bars = bars
        self.progress_kwargs: dict[str, object] = {
            "bars_fetched": len(bars),
            "pages": 1,
            "phase": "fetch",
        }
        self._connection = sqlite3.connect(path, check_same_thread=False)
        self._connection.execute(
            """CREATE TABLE bars (
                timeframe TEXT NOT NULL,
                open_time INTEGER NOT NULL,
                close_time INTEGER NOT NULL,
                open REAL NOT NULL, high REAL NOT NULL, low REAL NOT NULL,
                close REAL NOT NULL, volume REAL, PRIMARY KEY (timeframe, open_time)
            )"""
        )

    def _series(self, query: BarQuery, bars: tuple[Bar, ...], source: str) -> BarSeries:
        return BarSeries(
            query,
            bars,
            DataOrchestrator.coverage_for_series(query, bars, source),
        )

    def load_provider_series(self, query: BarQuery, progress_callback):
        bars = tuple(
            bar
            for bar in self._source_bars
            if query.start_ms <= bar.time < query.end_ms
        )
        progress_callback(**self.progress_kwargs)
        return self._series(query, bars, "provider")

    def load_bars(self, query: BarQuery) -> BarSeries:
        rows = self._connection.execute(
            """SELECT open_time, close_time, open, high, low, close, volume
               FROM bars WHERE timeframe = ? AND open_time >= ? AND open_time < ?
               ORDER BY open_time""",
            (query.timeframe.canonical, query.start_ms, query.end_ms),
        ).fetchall()
        bars = tuple(
            Bar(
                query.instrument,
                query.timeframe,
                int(row[0]),
                int(row[1]),
                float(row[2]),
                float(row[3]),
                float(row[4]),
                float(row[5]),
                None if row[6] is None else float(row[6]),
                True,
            )
            for row in rows
        )
        return self._series(query, bars, "storage")

    def store_bars(self, series: BarSeries):
        before = self._connection.total_changes
        self._connection.executemany(
            "INSERT OR IGNORE INTO bars VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    series.query.timeframe.canonical,
                    bar.time,
                    bar.time_close,
                    bar.open,
                    bar.high,
                    bar.low,
                    bar.close,
                    bar.volume,
                )
                for bar in series.bars
            ],
        )
        self._connection.commit()
        return SimpleNamespace(rows_written=self._connection.total_changes - before)

    def close(self) -> None:
        self._connection.close()


def _assert_chunked_backfill_derives_complete_higher_timeframe_bars_in_sqlite(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    minute = parse_timeframe("1m")
    five_minutes = parse_timeframe("5m")
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="BTCUSDT")
    source_bars = tuple(
        Bar(
            instrument,
            minute,
            index * 60_000,
            index * 60_000 + 59_999,
            100.0 + index,
            101.0 + index,
            99.0 + index,
            100.5 + index,
            10.0 + index,
            True,
        )
        for index in range(20)
    )
    orchestrator = _SqliteBarOrchestrator(tmp_path / "bars.sqlite", source_bars)
    state = SimpleNamespace(orchestrator=orchestrator)
    progress: list[tuple[object, ...]] = []
    monkeypatch.setattr(accounts_data, "_DATA_BACKFILL_SUBPROCESS_SOURCE_BARS", 6)
    payload: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "BTCUSDT",
        "timeframe": "5m",
        "from_time": 0,
        "to_time": 20 * 60_000,
        "execution_mode": "chunked",
    }

    try:
        result = accounts_data._run_data_backfill_chunked(
            payload=payload,
            state=state,
            progress_callback=lambda *args, **kwargs: progress.append((args, kwargs)),
            instrument=instrument,
            source_timeframe=minute,
            target_timeframe=five_minutes,
            start_ms=0,
            end_ms=20 * 60_000,
            estimated_source_bars=20,
        )
        stored_target = orchestrator.load_bars(
            BarQuery(instrument, five_minutes, 0, 20 * 60_000, gap_policy="allow_with_metadata")
        )
    finally:
        orchestrator.close()

    assert result["chunk_count"] == 4
    assert result["source_bars_loaded"] == 20
    assert result["target_bars_loaded"] == 4
    assert result["bars_loaded"] == 4
    assert result["coverage_complete"] is True
    assert [bar.time for bar in stored_target.bars] == [0, 300_000, 600_000, 900_000]
    assert [bar.close for bar in stored_target.bars] == [104.5, 109.5, 114.5, 119.5]
    assert len(progress) == 12


def _assert_chunked_source_backfill_keeps_source_summary_compact(tmp_path: Path, monkeypatch) -> None:
    minute = parse_timeframe("1m")
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="ETHUSDT")
    source_bars = tuple(
        Bar(
            instrument,
            minute,
            index * 60_000,
            index * 60_000 + 59_999,
            10.0 + index,
            11.0 + index,
            9.0 + index,
            10.5 + index,
            1.0,
            True,
        )
        for index in range(9)
    )
    orchestrator = _SqliteBarOrchestrator(tmp_path / "source.sqlite", source_bars)
    monkeypatch.setattr(accounts_data, "_DATA_BACKFILL_SUBPROCESS_SOURCE_BARS", 4)
    try:
        result = accounts_data._run_data_backfill_chunked(
            payload={"execution_mode": "chunked"},
            state=SimpleNamespace(orchestrator=orchestrator),
            progress_callback=lambda *_args, **_kwargs: None,
            instrument=instrument,
            source_timeframe=minute,
            target_timeframe=minute,
            start_ms=0,
            end_ms=9 * 60_000,
            estimated_source_bars=9,
        )
    finally:
        orchestrator.close()

    assert result["bars_loaded"] == 9
    assert result["chunk_count"] == 3
    assert result["coverage_complete"] is True
    assert "source_bars_loaded" not in result
    assert "target_bars_loaded" not in result


class _BrokenEvent:
    def set(self) -> None:
        raise OSError("event channel unavailable")

    def is_set(self) -> bool:
        raise OSError("event channel unavailable")


class _BrokenHeartbeat:
    @property
    def value(self) -> float:
        raise OSError("heartbeat unavailable")


class _StuckProcess:
    pid = 77

    def start(self) -> None:
        return None

    def is_alive(self) -> bool:
        return True

    def join(self, _timeout: float | None = None) -> None:
        raise OSError("join unavailable")

    def terminate(self) -> None:
        raise OSError("terminate unavailable")

    def kill(self) -> None:
        raise OSError("kill unavailable")

    @property
    def exitcode(self) -> int:
        raise OSError("exitcode unavailable")


def _assert_worker_supervisor_reports_broken_shutdown_control_plane() -> None:
    asyncio.run(_assert_broken_shutdown_control_plane())


async def _assert_broken_shutdown_control_plane() -> None:
    process = _StuckProcess()
    supervisor = WorkerSupervisor(
        lambda: (process, _BrokenEvent(), _BrokenEvent(), _BrokenHeartbeat()),
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=60.0,
            shutdown_timeout_seconds=0.001,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )

    supervisor.start()
    snapshot = supervisor.snapshot()
    safe_to_close = await supervisor.stop()

    assert snapshot["alive"] is True
    assert snapshot["ready"] is False
    assert snapshot["heartbeat_stale"] is True
    assert snapshot["reason"] == "process_status_failed"
    assert safe_to_close is False
    final = supervisor.snapshot()
    assert final["alive"] is True
    assert final["degraded"] is True
    assert final["reason"] == "process_status_failed"


def _assert_worker_supervisor_rejects_unsafe_policy_values(values, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        SupervisorConfig(**values)


def _assert_worker_supervisor_rejects_malformed_factory_without_trusting_legacy_liveness() -> None:
    supervisor = WorkerSupervisor(
        lambda: (object(), object(), object()),
        fail_safe=lambda: None,
    )
    assert supervisor.process is None
    assert supervisor._start_process() is False
    assert supervisor.snapshot()["reason"] == "process_factory_failed"

    class BrokenLegacyProcess:
        pid = 9
        exitcode = None

        def is_alive(self) -> bool:
            raise OSError("legacy status channel failed")

    status = worker_runtime_snapshot(
        SimpleNamespace(
            _background_worker_supervisor=None,
            _background_worker_process=BrokenLegacyProcess(),
        )
    )
    assert status["alive"] is None
    assert status["liveness"] == "unknown"


def _assert_worker_supervisor_start_is_idempotent_for_a_live_child() -> None:
    asyncio.run(_assert_start_is_idempotent())


async def _assert_start_is_idempotent() -> None:
    class Process:
        pid = 11
        exitcode = None

        def __init__(self) -> None:
            self.alive = False

        def start(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, _timeout: float) -> None:
            self.alive = False

        def terminate(self) -> None:
            self.alive = False

        def kill(self) -> None:
            self.alive = False

    created: list[Process] = []

    def factory():
        process = Process()
        created.append(process)
        return process, SimpleNamespace(set=lambda: None)

    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: None,
        config=SupervisorConfig(poll_interval_seconds=60.0),
    )
    supervisor.start()
    supervisor.start()
    assert len(created) == 1
    assert await supervisor.stop() is True


def _assert_worker_supervisor_containment_waits_for_fail_safe_and_reports_unstoppable_child() -> None:
    asyncio.run(_assert_fail_safe_and_unstoppable_child_behavior())


async def _assert_fail_safe_and_unstoppable_child_behavior() -> None:
    supervisor = WorkerSupervisor(
        lambda: (_StuckProcess(), _BrokenEvent()),
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=60.0,
            fail_safe_attempt_timeout_seconds=0.001,
            terminate_timeout_seconds=0.001,
            kill_timeout_seconds=0.001,
        ),
    )
    release = asyncio.Event()
    active = asyncio.create_task(release.wait())
    supervisor._fail_safe_task = active
    await supervisor._invoke_fail_safe_safely()
    assert supervisor.snapshot()["fail_safe_status"] == "running"
    release.set()
    await active

    async def fail_safe_task() -> None:
        raise RuntimeError("fail-safe task failed")

    failed_task = asyncio.create_task(fail_safe_task())
    supervisor._fail_safe_task = failed_task
    assert await supervisor._wait_for_fail_safe_before_restart() is False

    another_failed_task = asyncio.create_task(fail_safe_task())
    supervisor._fail_safe_task = another_failed_task
    assert await supervisor._await_fail_safe_completion() is False

    cancelled_task = asyncio.create_task(asyncio.Event().wait())
    cancelled_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await cancelled_task
    supervisor._fail_safe_task = cancelled_task
    supervisor._finish_fail_safe_task(cancelled_task)
    assert supervisor.snapshot()["fail_safe_status"] == "failed"

    assert await supervisor._terminate_unhealthy_process(_StuckProcess()) is False
    snapshot = supervisor.snapshot()
    assert snapshot["degraded"] is True
    assert snapshot["reason"] == "unhealthy_process_shutdown_incomplete"


def _assert_worker_supervisor_restarts_when_runtime_process_reference_is_lost() -> None:
    asyncio.run(_assert_missing_process_is_restarted())


async def _assert_missing_process_is_restarted() -> None:
    processes: list[SimpleNamespace] = []

    class Process:
        def __init__(self) -> None:
            self.pid = len(processes) + 1
            self.exitcode = None
            self.alive = False

        def start(self) -> None:
            self.alive = True

        def is_alive(self) -> bool:
            return self.alive

        def join(self, _timeout: float) -> None:
            return None

        def terminate(self) -> None:
            self.alive = False

        def kill(self) -> None:
            self.alive = False

    def factory():
        process = Process()
        processes.append(process)
        return process, SimpleNamespace(set=lambda: None, is_set=lambda: False)

    supervisor = WorkerSupervisor(
        factory,
        fail_safe=lambda: None,
        config=SupervisorConfig(
            poll_interval_seconds=0.001,
            backoff_initial_seconds=0.0,
            backoff_max_seconds=0.0,
        ),
    )
    supervisor.start()
    supervisor._process = None
    async with asyncio.timeout(1.0):
        while len(processes) != 2:
            await asyncio.sleep(0.001)
    assert supervisor.snapshot()["reason"] == "worker_restarted"
    await supervisor.stop()


def _assert_backfill_runner_records_real_provider_progress_and_terminal_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    minute = parse_timeframe("1m")
    instrument = InstrumentKey(exchange="binance", market="spot", symbol="SOLUSDT")
    bars = tuple(
        Bar(instrument, minute, index * 60_000, index * 60_000 + 59_999, 1, 2, 0, 1, 1, True)
        for index in range(5)
    )
    orchestrator = _SqliteBarOrchestrator(tmp_path / "runner.sqlite", bars)
    orchestrator.progress_kwargs = {
        "bars_fetched": "not-an-int",
        "pages": "not-an-int",
        "total_bars": "not-an-int",
        "total_pages": "not-an-int",
        "phase": "write",
    }
    scheduler = JobScheduler()
    job = scheduler.enqueue(
        Job(
            id="backfill-real-progress",
            job_type=JobType.BACKFILL,
            idempotency_key="backfill-real-progress",
        )
    )
    monkeypatch.setattr(accounts_data, "_DATA_BACKFILL_SUBPROCESS_SOURCE_BARS", 100)
    payload: dict[str, object] = {
        "exchange": "binance",
        "market_type": "spot",
        "symbol": "SOLUSDT",
        "timeframe": "1m",
        "from_time": 0,
        "to_time": 5 * 60_000,
        "estimated_source_bars": 5,
    }
    state = SimpleNamespace(
        scheduler=scheduler,
        orchestrator=orchestrator,
        config=SimpleNamespace(data_dir=tmp_path, data_cache_root=tmp_path / "cache"),
    )
    try:
        asyncio.run(accounts_data._run_data_backfill_job(job.id, payload, state))
        completed = scheduler.get_job(job.id)
    finally:
        orchestrator.close()

    assert completed is not None
    assert completed.status is JobStatus.DONE
    assert completed.result is not None
    assert completed.result["bars_loaded"] == 5


def _assert_background_dispatch_records_failed_paper_and_blocked_live_jobs_in_sqlite(
    tmp_path: Path,
) -> None:
    paper = Job(
        id="paper-failure",
        strategy_id="paper-strategy",
        job_type=JobType.PAPER_BAR_PROCESS,
        idempotency_key="paper-failure-key",
        input={"strategy_id": "paper-strategy", "artifact_id": "artifact-paper"},
    )
    live = Job(
        id="live-blocked",
        strategy_id="live-strategy",
        job_type=JobType.LIVE_BAR_PROCESS,
        idempotency_key="live-blocked-key",
        input={"strategy_id": "live-strategy", "artifact_id": "artifact-live"},
    )
    store = JobV1Store(tmp_path / "gateway-jobs.sqlite")
    failed: list[tuple[str, str]] = []
    try:
        server._process_refreshed_strategy_bars(
            fanout=SimpleNamespace(
                process_source_bar=lambda _bar: SimpleNamespace(jobs=(paper, live))
            ),
            executor=SimpleNamespace(
                process=lambda job: SimpleNamespace(
                    status="failed",
                    strategy_id=job.strategy_id,
                    error="execution rejected",
                    bar_time=60_000,
                    trades_recorded=0,
                )
            ),
            scheduler=SimpleNamespace(
                mark_failed=lambda job_id, reason: failed.append((job_id, reason))
            ),
            market_key="binance:spot:BTCUSDT:trade",
            bars=[SimpleNamespace(time=60_000)],
            job_store=store,
            stack_id="sha256:" + "b" * 64,
            lease_owner="gateway-parent-path-test",
        )
        persisted_paper = store.get("paper-failure")
        persisted_live = store.get("live-blocked")
    finally:
        store.close()

    assert persisted_paper["state"] == "FAILED"
    assert persisted_paper["error_code"] == "execution rejected"
    assert persisted_live["state"] == "FAILED"
    assert persisted_live["error_code"] == "LIVE_RC_BLOCKED"
    assert failed == [("live-blocked", "LIVE_RC_BLOCKED")]


def test_gateway_parent_paths_with_real_sqlite_backfills(tmp_path: Path, monkeypatch) -> None:
    _assert_chunked_backfill_derives_complete_higher_timeframe_bars_in_sqlite(
        tmp_path, monkeypatch
    )
    _assert_chunked_source_backfill_keeps_source_summary_compact(tmp_path, monkeypatch)
    _assert_backfill_runner_records_real_provider_progress_and_terminal_result(
        tmp_path, monkeypatch
    )
    _assert_background_dispatch_records_failed_paper_and_blocked_live_jobs_in_sqlite(tmp_path)


def test_worker_supervisor_parent_paths_fail_closed() -> None:
    _assert_worker_supervisor_reports_broken_shutdown_control_plane()
    for values, message in (
        ({"poll_interval_seconds": 0}, "poll_interval_seconds"),
        ({"backoff_initial_seconds": -1}, "restart backoff"),
        ({"max_restarts": -1}, "max_restarts"),
        ({"restart_window_seconds": 0}, "restart_window"),
        ({"kill_timeout_seconds": -1}, "shutdown timeouts"),
        ({"heartbeat_stale_seconds": 0}, "heartbeat_stale"),
        ({"startup_readiness_timeout_seconds": 0}, "startup_readiness"),
        ({"fail_safe_max_attempts": 0}, "fail-safe retry"),
        ({"fail_safe_attempt_timeout_seconds": 0}, "fail_safe_attempt"),
    ):
        _assert_worker_supervisor_rejects_unsafe_policy_values(values, message)
    _assert_worker_supervisor_rejects_malformed_factory_without_trusting_legacy_liveness()
    _assert_worker_supervisor_start_is_idempotent_for_a_live_child()
    _assert_worker_supervisor_containment_waits_for_fail_safe_and_reports_unstoppable_child()
    _assert_worker_supervisor_restarts_when_runtime_process_reference_is_lost()
