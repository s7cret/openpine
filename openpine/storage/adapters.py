"""Storage backend adapters for OpenPine.

Each adapter represents a storage role:
- SQLiteControlStorageAdapter — control/state/jobs/orders/events/metadata/manifests
- ParquetDataLakeAdapter — OHLCV/history/features/reports/backtest outputs
- DuckDBAnalyticsAdapter — analytics/query over Parquet
- PostgresControlStorageAdapter — production control adapter (same contracts as SQLite)
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

# --------------------------------------------------------------------------- #
# Shared types
# --------------------------------------------------------------------------- #


class BackendRole(Enum):
    """Storage role labels."""

    CONTROL = (
        "control"  # SQLite or Postgres — control/state/jobs/orders/events/metadata
    )
    DATA_LAKE = "data_lake"  # Parquet — OHLCV/history/features/reports/backtest outputs
    ANALYTICS = "analytics"  # DuckDB — analytics/query layer


class BackendHealth(Enum):
    """Health states for a storage backend."""

    AVAILABLE = "available"
    UNAVAILABLE_MISSING_DEPS = "unavailable_missing_deps"
    UNAVAILABLE_CONFIG = "unavailable_config"
    UNAVAILABLE_ERROR = "unavailable_error"


@dataclass
class BackendInfo:
    """Information about a storage backend."""

    name: str
    role: BackendRole
    health: BackendHealth
    version: str | None = None
    error: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# StorageBackend protocol
# --------------------------------------------------------------------------- #


class StorageBackend(ABC):
    """Abstract base for all storage backends."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Short identifier for this backend (e.g. 'sqlite', 'parquet')."""

    @property
    @abstractmethod
    def role(self) -> BackendRole:
        """Which storage role this backend fulfils."""

    @abstractmethod
    def available(self) -> bool:
        """Return True when the backend can be used."""

    @abstractmethod
    def health_check(self) -> BackendInfo:
        """Return a BackendInfo describing current health."""


# --------------------------------------------------------------------------- #
# SQLite — control / state / jobs / orders / events / metadata / manifests
# --------------------------------------------------------------------------- #


class SQLiteControlStorageAdapter(StorageBackend):
    """SQLite-backed control plane storage.

    This is the primary local MVP backend for all control-plane data:
    jobs, orders, events, strategy instances, accounts, manifests, and
    schema metadata.
    """

    def __init__(self, db_path: Path | None = None) -> None:
        from openpine.config import OpenPineConfig

        config = OpenPineConfig.load()
        self._db_path: Path = db_path or config.sqlite_path
        self._storage: Any = None  # set lazily on first use

    @property
    def name(self) -> str:
        return "sqlite"

    @property
    def role(self) -> BackendRole:
        return BackendRole.CONTROL

    def _get_storage(self) -> Any:
        """Lazily open the SQLiteStorage."""
        if self._storage is None:
            from openpine.storage.sqlite_storage import SQLiteStorage

            self._storage = SQLiteStorage(self._db_path)
        return self._storage

    def available(self) -> bool:
        """SQLite is available if the file is accessible (or writable parent exists)."""
        try:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            return True
        except OSError:
            return False

    def health_check(self) -> BackendInfo:
        try:
            storage = self._get_storage()
            # Light query to verify the connection is alive
            storage.execute("SELECT 1")
            return BackendInfo(
                name=self.name,
                role=self.role,
                health=BackendHealth.AVAILABLE,
                extra={"db_path": str(self._db_path)},
            )
        except Exception as exc:  # pragma: no cover — local MVP
            return BackendInfo(
                name=self.name,
                role=self.role,
                health=BackendHealth.UNAVAILABLE_ERROR,
                error=str(exc),
            )

    def close(self) -> None:
        if self._storage is not None:
            self._storage.close()
            self._storage = None


# --------------------------------------------------------------------------- #
# Parquet — data lake / OHLCV / history / features / reports / backtest outputs
# --------------------------------------------------------------------------- #


class ParquetDataLakeAdapter(StorageBackend):
    """Parquet-based data lake for historical and large-format data.

    Uses pyarrow when available and a local pandas-backed fallback for hermetic
    development gates. Production deployments should install pyarrow for true
    parquet interoperability.

    Role: DATA_LAKE
    """

    def __init__(self, data_dir: Path | None = None) -> None:
        from openpine.config import OpenPineConfig

        config = OpenPineConfig.load()
        self._data_dir: Path = data_dir or (config.data_dir / "parquet_lake")
        self._pyarrow_available: bool = self._check_pyarrow()

    def _check_pyarrow(self) -> bool:
        from openpine._compat.parquet import pyarrow_available

        return pyarrow_available()

    @property
    def name(self) -> str:
        return "parquet"

    @property
    def role(self) -> BackendRole:
        return BackendRole.DATA_LAKE

    def available(self) -> bool:
        """Parquet lake is available when its data directory is writable."""
        try:
            self._data_dir.mkdir(parents=True, exist_ok=True)
            probe = self._data_dir / ".probe"
            probe.write_text("ok")
            probe.unlink()
            return True
        except OSError:
            return False

    def health_check(self) -> BackendInfo:
        try:
            if not self.available():
                return BackendInfo(
                    name=self.name,
                    role=self.role,
                    health=BackendHealth.UNAVAILABLE_CONFIG,
                    error="data directory not writable",
                )

            # Read basic metadata from a known path if present.  Schema details
            # are only available with pyarrow; the fallback still exposes the
            # latest file for diagnostics.
            schema_extra = {}
            latest = self._latest_parquet_file()
            if latest:
                schema_extra["latest_file"] = str(latest)
                if self._pyarrow_available:
                    import pyarrow.parquet as pq

                    pf = pq.ParquetFile(latest)
                    schema_extra["schema"] = str(pf.schema_arrow)
                else:
                    from openpine._compat import parquet

                    parquet.read_dataframe(latest)
                    schema_extra["schema"] = "pandas-fallback"

            return BackendInfo(
                name=self.name,
                role=self.role,
                health=BackendHealth.AVAILABLE,
                version="pyarrow" if self._pyarrow_available else "pandas-fallback",
                extra={"backend": "parquet", **schema_extra},
            )
        except Exception as exc:
            return BackendInfo(
                name=self.name,
                role=self.role,
                health=BackendHealth.UNAVAILABLE_ERROR,
                error=str(exc),
            )

    def _latest_parquet_file(self) -> Path | None:
        candidates = list(self._data_dir.glob("**/*.parquet"))
        if not candidates:
            return None
        return max(candidates, key=lambda p: p.stat().st_mtime)

    # ------------------------------------------------------------------ #
    # Data-plane write API (used by orchestrator / backtest output)
    # ------------------------------------------------------------------ #

    def write_ohlcv(self, symbol: str, timeframe: str, bars: list[dict]) -> None:
        """Write OHLCV bars to the lake.

        Uses Parquet only. Diagnostic JSONL exports must be explicit elsewhere.
        """
        import datetime

        date_str = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
        prefix = f"{symbol.replace('/', '_')}_{timeframe}_{date_str}"
        self._write_ohlcv_parquet(prefix, bars)

    def _write_ohlcv_parquet(self, prefix: str, bars: list[dict]) -> None:
        import pandas as pd

        from openpine._compat import parquet

        path = self._data_dir / f"{prefix}.parquet"
        parquet.write_dataframe(pd.DataFrame(bars), path)

    def read_ohlcv(
        self, symbol: str, timeframe: str, start_ts: int, end_ts: int
    ) -> list[dict]:
        """Read OHLCV bars from the lake."""
        return self._read_ohlcv_parquet(symbol, timeframe, start_ts, end_ts)

    def _read_ohlcv_parquet(
        self, symbol: str, timeframe: str, start_ts: int, end_ts: int
    ) -> list[dict]:
        from openpine._compat import parquet

        prefix = f"{symbol.replace('/', '_')}_{timeframe}_"
        matches = list(self._data_dir.glob(f"{prefix}*.parquet"))
        results: list[dict] = []
        for path in matches:
            records = parquet.read_dataframe(path).to_dict("records")
            for bar in records:
                ts = int(bar.get("timestamp", 0))
                if start_ts <= ts < end_ts:
                    results.append(bar)
        return results


# --------------------------------------------------------------------------- #
# DuckDB — analytics / query layer over Parquet
# --------------------------------------------------------------------------- #


class DuckDBAnalyticsAdapter(StorageBackend):
    """DuckDB-backed analytics layer.

    Queries over Parquet data lake for ad-hoc and scripted analytics.
    Fail-closed when duckdb is not installed.

    Role: ANALYTICS
    """

    def __init__(
        self, db_path: Path | None = None, data_dir: Path | None = None
    ) -> None:
        from openpine.config import OpenPineConfig

        config = OpenPineConfig.load()
        self._db_path: Path = db_path or config.duckdb_path
        self._data_dir: Path = data_dir or (config.data_dir / "parquet_lake")
        self._duckdb_available: bool = self._check_duckdb()
        self._conn: Any = None

    def _check_duckdb(self) -> bool:
        try:
            import duckdb  # noqa: F401

            return True
        except ImportError:
            return False

    @property
    def name(self) -> str:
        return "duckdb"

    @property
    def role(self) -> BackendRole:
        return BackendRole.ANALYTICS

    def available(self) -> bool:
        if not self._duckdb_available:
            return False
        try:
            conn = self._get_conn()
            conn.execute("SELECT 1")
            return True
        except Exception:
            return False

    def health_check(self) -> BackendInfo:
        if not self._duckdb_available:
            return BackendInfo(
                name=self.name,
                role=self.role,
                health=BackendHealth.UNAVAILABLE_MISSING_DEPS,
                error="duckdb not installed (pip install duckdb)",
            )

        try:
            conn = self._get_conn()
            conn.execute("SELECT 1")

            parquet_count: int | None = 0
            parquet_count_error: str | None = None
            try:
                glob_pattern = f"{self._data_dir}/**/*.parquet"
                result = conn.execute(
                    "SELECT COUNT(*) FROM glob(?)", (glob_pattern,)
                ).fetchone()
                parquet_count = result[0] if result else 0
            except Exception as exc:
                parquet_count = None
                parquet_count_error = str(exc)

            extra = {
                "db_path": str(self._db_path),
                "data_dir": str(self._data_dir),
                "parquet_files": parquet_count,
            }
            if parquet_count_error:
                extra["parquet_files_error"] = parquet_count_error

            return BackendInfo(
                name=self.name,
                role=self.role,
                health=BackendHealth.AVAILABLE,
                version=self._duckdb_version(),
                extra=extra,
            )
        except Exception as exc:
            return BackendInfo(
                name=self.name,
                role=self.role,
                health=BackendHealth.UNAVAILABLE_ERROR,
                error=str(exc),
            )

    def _get_conn(self) -> Any:
        if self._conn is None:
            import duckdb

            self._conn = duckdb.connect(str(self._db_path))
        return self._conn

    def _duckdb_version(self) -> str:
        try:
            import duckdb

            return duckdb.__version__
        except Exception:
            return "unknown"

    def query(self, sql: str, params: tuple = ()) -> list[tuple]:
        """Execute a read-only analytical query."""
        if not self._duckdb_available:
            raise RuntimeError("duckdb not installed — cannot execute query")
        conn = self._get_conn()
        result = conn.execute(sql, params).fetchall()
        return result

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# --------------------------------------------------------------------------- #
# Postgres — production control adapter
# --------------------------------------------------------------------------- #


class PostgresControlStorageAdapter(StorageBackend):
    """Postgres-backed control plane storage.

    Provides the same contract as SQLiteControlStorageAdapter but backed by
    a Postgres database for production deployments.
    Fail-closed when psycopg is not installed.

    Role: CONTROL
    """

    def __init__(
        self,
        host: str | None = None,
        port: int | None = None,
        dbname: str | None = None,
        user: str | None = None,
        password: str | None = None,
    ) -> None:
        # Accept explicit connection params or load from config
        from openpine.config import OpenPineConfig

        config = OpenPineConfig.load()
        self._host: str = host or getattr(config, "postgres_host", "localhost")
        self._port: int = port or getattr(config, "postgres_port", 5432)
        self._dbname: str = dbname or getattr(config, "postgres_db", "openpine")
        self._user: str = user or getattr(config, "postgres_user", "openpine")
        self._password: str = password or getattr(config, "postgres_password", "")
        self._psycopg_available: bool = self._check_psycopg()
        self._conn: Any = None

    def _check_psycopg(self) -> bool:
        try:
            import psycopg  # noqa: F401

            return True
        except ImportError:
            return False

    @property
    def name(self) -> str:
        return "postgres"

    @property
    def role(self) -> BackendRole:
        return BackendRole.CONTROL

    def available(self) -> bool:
        if not self._psycopg_available:
            return False
        try:
            return self._health_check_impl() is None
        except Exception:
            return False

    def health_check(self) -> BackendInfo:
        if not self._psycopg_available:
            return BackendInfo(
                name=self.name,
                role=self.role,
                health=BackendHealth.UNAVAILABLE_MISSING_DEPS,
                error="psycopg not installed (pip install psycopg)",
            )

        try:
            error = self._health_check_impl()
            if error is None:
                return BackendInfo(
                    name=self.name,
                    role=self.role,
                    health=BackendHealth.AVAILABLE,
                    version=self._psycopg_version(),
                    extra={
                        "host": self._host,
                        "port": self._port,
                        "dbname": self._dbname,
                    },
                )
            else:
                return BackendInfo(
                    name=self.name,
                    role=self.role,
                    health=BackendHealth.UNAVAILABLE_ERROR,
                    error=error,
                )
        except Exception as exc:
            return BackendInfo(
                name=self.name,
                role=self.role,
                health=BackendHealth.UNAVAILABLE_ERROR,
                error=str(exc),
            )

    def _health_check_impl(self) -> str | None:
        """Return None if healthy, error message otherwise."""
        try:
            import psycopg

            with psycopg.connect(
                host=self._host,
                port=self._port,
                dbname=self._dbname,
                user=self._user,
                password=self._password,
                connect_timeout=3,
            ) as conn:
                conn.execute("SELECT 1")
            return None
        except ImportError:
            return "psycopg not installed"
        except Exception as exc:
            return str(exc)

    def _psycopg_version(self) -> str:
        try:
            import psycopg

            return psycopg.__version__
        except Exception:
            return "unknown"
