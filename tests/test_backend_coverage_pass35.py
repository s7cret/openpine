from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

from openpine.config import OpenPineConfig
from openpine.storage import adapters


def _patch_config(monkeypatch, tmp_path: Path):
    cfg = OpenPineConfig(
        config_dir=tmp_path / "cfg",
        data_dir=tmp_path / "data",
        sqlite_path=tmp_path / "data" / "control.sqlite",
        duckdb_path=tmp_path / "data" / "analytics.duckdb",
    )
    import openpine.config as config_mod

    monkeypatch.setattr(config_mod.OpenPineConfig, "load", staticmethod(lambda: cfg))
    return cfg


def test_sqlite_and_parquet_storage_adapter_error_edges(monkeypatch, tmp_path: Path):
    _patch_config(monkeypatch, tmp_path)

    file_parent = tmp_path / "not_a_dir"
    file_parent.write_text("x", encoding="utf-8")
    sqlite = adapters.SQLiteControlStorageAdapter(file_parent / "db.sqlite")
    assert sqlite.available() is False
    sqlite._get_storage = lambda: (_ for _ in ()).throw(RuntimeError("broken sqlite"))
    health = sqlite.health_check()
    assert health.health is adapters.BackendHealth.UNAVAILABLE_ERROR

    parquet = adapters.ParquetDataLakeAdapter(file_parent / "lake")
    assert parquet.available() is False
    health = parquet.health_check()
    assert health.health is adapters.BackendHealth.UNAVAILABLE_CONFIG

    lake = tmp_path / "lake"
    parquet = adapters.ParquetDataLakeAdapter(lake)
    bad = lake / "BTC_1m_bad.parquet"
    bad.parent.mkdir(parents=True, exist_ok=True)
    bad.write_bytes(b"not a parquet fallback")
    health = parquet.health_check()
    assert health.health in {
        adapters.BackendHealth.AVAILABLE,
        adapters.BackendHealth.UNAVAILABLE_ERROR,
    }
    assert parquet.read_ohlcv("MISSING", "1m", 0, 1) == []


def test_duckdb_storage_adapter_available_query_and_error_edges(monkeypatch, tmp_path: Path):
    cfg = _patch_config(monkeypatch, tmp_path)

    class Conn:
        def __init__(self):
            self.closed = False
        def execute(self, sql, params=()):
            if "glob" in sql:
                return SimpleNamespace(fetchone=lambda: (3,))
            if "boom" in sql:
                raise RuntimeError("query boom")
            return SimpleNamespace(fetchall=lambda: [(1,)], fetchone=lambda: (1,))
        def close(self):
            self.closed = True

    conn = Conn()
    duckdb = ModuleType("duckdb")
    duckdb.__version__ = "test-duck"
    duckdb.connect = lambda path: conn
    monkeypatch.setitem(sys.modules, "duckdb", duckdb)

    adapter = adapters.DuckDBAnalyticsAdapter(cfg.duckdb_path, cfg.data_dir / "lake")
    assert adapter.available() is True
    health = adapter.health_check()
    assert health.health is adapters.BackendHealth.AVAILABLE
    assert health.version == "test-duck"
    assert adapter.query("select 1") == [(1,)]
    adapter.close()
    assert adapter._conn is None

    duckdb.connect = lambda path: (_ for _ in ()).throw(RuntimeError("connect boom"))
    broken = adapters.DuckDBAnalyticsAdapter(cfg.duckdb_path, cfg.data_dir / "lake")
    assert broken.available() is False
    assert broken.health_check().health is adapters.BackendHealth.UNAVAILABLE_ERROR


def test_postgres_storage_adapter_success_error_and_version_edges(monkeypatch, tmp_path: Path):
    _patch_config(monkeypatch, tmp_path)

    class Conn:
        def __enter__(self):
            return self
        def __exit__(self, *args):
            return False
        def execute(self, sql):
            return None

    psycopg = ModuleType("psycopg")
    psycopg.__version__ = "test-pg"
    psycopg.connect = lambda **kwargs: Conn()
    monkeypatch.setitem(sys.modules, "psycopg", psycopg)

    adapter = adapters.PostgresControlStorageAdapter(host="h", dbname="d", user="u")
    assert adapter.available() is True
    health = adapter.health_check()
    assert health.health is adapters.BackendHealth.AVAILABLE
    assert health.version == "test-pg"

    psycopg.connect = lambda **kwargs: (_ for _ in ()).throw(RuntimeError("pg down"))
    down = adapters.PostgresControlStorageAdapter()
    assert down.available() is False
    health = down.health_check()
    assert health.health is adapters.BackendHealth.UNAVAILABLE_ERROR
    assert "pg down" in (health.error or "")

    delattr(psycopg, "__version__")
    assert down._psycopg_version() == "unknown"
