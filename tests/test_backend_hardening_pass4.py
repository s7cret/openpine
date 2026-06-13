from __future__ import annotations

import sys
import types
from pathlib import Path

import pytest

from openpine.jobs.retry import RetryPolicy
from openpine.jobs.scheduler import JobScheduler
from openpine.storage.adapters import (
    BackendHealth,
    BackendRole,
    DuckDBAnalyticsAdapter,
    ParquetDataLakeAdapter,
    PostgresControlStorageAdapter,
    SQLiteControlStorageAdapter,
)
from openpine.workers.pool import (
    AggregationWorkerPool,
    FeatureWorkerPool,
    WorkerPool,
    WorkerStatus,
)


def test_worker_pool_lifecycle_and_heartbeat(monkeypatch):
    now = iter([1000.0, 1001.0, 1002.0, 1003.0, 1004.0])
    monkeypatch.setattr("openpine.workers.pool.time.time", lambda: next(now))
    scheduler = JobScheduler()
    pool = WorkerPool(scheduler, max_workers=3)
    pool.start()
    pool.register_worker("w1")
    pool.worker_heartbeat("w1")
    status = pool.get_status()
    assert status["running"] is True
    assert status["max_workers"] == 3
    assert status["active_workers"] == 1
    assert "w1" in status["heartbeats"]
    pool.stop()
    assert pool.get_status()["running"] is False
    assert WorkerStatus.IDLE.value == "idle"
    assert AggregationWorkerPool(scheduler).JOB_TYPES
    assert FeatureWorkerPool(scheduler).JOB_TYPES


def test_retry_policy_bounds():
    policy = RetryPolicy(max_attempts=3, base_delay_seconds=1.5, max_delay_seconds=3.0)
    assert policy.get_delay(0) == 0.0
    assert policy.get_delay(1) == 1.5
    assert policy.get_delay(3) == 3.0
    assert policy.should_retry(2) is True
    assert policy.should_retry(3) is False


def test_sqlite_and_parquet_storage_adapters(tmp_path: Path):
    sqlite = SQLiteControlStorageAdapter(tmp_path / "control.sqlite")
    assert sqlite.name == "sqlite"
    assert sqlite.role == BackendRole.CONTROL
    assert sqlite.available() is True
    info = sqlite.health_check()
    assert info.health == BackendHealth.AVAILABLE
    sqlite.close()

    parquet = ParquetDataLakeAdapter(tmp_path / "lake")
    assert parquet.name == "parquet"
    assert parquet.role == BackendRole.DATA_LAKE
    assert parquet.available() is True
    parquet.write_ohlcv(
        "BTC/USDT",
        "1m",
        [
            {"timestamp": 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5},
            {"timestamp": 2000, "open": 2.0, "high": 3.0, "low": 1.5, "close": 2.5},
        ],
    )
    rows = parquet.read_ohlcv("BTC/USDT", "1m", 0, 1500)
    assert len(rows) == 1 and rows[0]["timestamp"] == 1000
    assert parquet.health_check().health == BackendHealth.AVAILABLE

    fallback_parquet = ParquetDataLakeAdapter(tmp_path / "fallback")
    fallback_parquet._pyarrow_available = False
    assert fallback_parquet.available() is True
    fallback_info = fallback_parquet.health_check()
    assert fallback_info.health == BackendHealth.AVAILABLE
    assert fallback_info.version == "pandas-fallback"
    fallback_parquet.write_ohlcv(
        "ETH/USDT",
        "1m",
        [{"timestamp": 1000, "open": 1.0, "high": 2.0, "low": 0.5, "close": 1.5}],
    )
    assert fallback_parquet.read_ohlcv("ETH/USDT", "1m", 0, 2000)[0]["timestamp"] == 1000


def test_duckdb_and_postgres_adapters_missing_and_fake_modules(
    monkeypatch, tmp_path: Path
):
    duck = DuckDBAnalyticsAdapter(tmp_path / "analytics.duckdb", tmp_path / "lake")
    duck._duckdb_available = False
    assert duck.available() is False
    assert duck.health_check().health == BackendHealth.UNAVAILABLE_MISSING_DEPS
    with pytest.raises(RuntimeError, match="duckdb"):
        duck.query("select 1")

    class FakeDuckConnection:
        def __init__(self) -> None:
            self.closed = False

        def execute(self, sql, params=()):
            self.sql = sql
            return self

        def fetchone(self):
            return (0,)

        def fetchall(self):
            return [(1,)]

        def close(self):
            self.closed = True

    fake_duck = types.SimpleNamespace(
        __version__="1.0", connect=lambda path: FakeDuckConnection()
    )
    monkeypatch.setitem(sys.modules, "duckdb", fake_duck)
    duck = DuckDBAnalyticsAdapter(tmp_path / "analytics2.duckdb", tmp_path / "lake")
    assert duck.available() is True
    assert duck.query("select ?", (1,)) == [(1,)]
    assert duck.health_check().version == "1.0"
    duck.close()

    pg = PostgresControlStorageAdapter(host="localhost")
    pg._psycopg_available = False
    assert pg.available() is False
    assert pg.health_check().health == BackendHealth.UNAVAILABLE_MISSING_DEPS

    class FakePsycopgConnection:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return None

        def execute(self, sql):
            return None

    fake_psycopg = types.SimpleNamespace(
        __version__="3.0",
        connect=lambda **kwargs: FakePsycopgConnection(),
    )
    monkeypatch.setitem(sys.modules, "psycopg", fake_psycopg)
    pg = PostgresControlStorageAdapter(host="localhost")
    assert pg.available() is True
    pg_info = pg.health_check()
    assert pg_info.health == BackendHealth.AVAILABLE
    assert pg_info.version == "3.0"
