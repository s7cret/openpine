"""OpenPine storage layer."""

from openpine.storage.adapters import (
    BackendHealth,
    BackendInfo,
    BackendRole,
    DuckDBAnalyticsAdapter,
    ParquetDataLakeAdapter,
    PostgresControlStorageAdapter,
    SQLiteControlStorageAdapter,
    StorageBackend,
)
from openpine.storage.backends import StorageBackendRegistry
from openpine.storage.backup import backup_openpine, restore_openpine, verify_openpine
from openpine.storage.manifests import ManifestStore
from openpine.storage.migrations import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage

from openpine.storage.backtest_storage import BacktestStorage

__all__ = [
    # Core SQLite
    "SQLiteStorage",
    "MigrationRunner",
    "ManifestStore",
    "BacktestStorage",
    # Adapters
    "SQLiteControlStorageAdapter",
    "ParquetDataLakeAdapter",
    "DuckDBAnalyticsAdapter",
    "PostgresControlStorageAdapter",
    "StorageBackend",
    "BackendRole",
    "BackendHealth",
    "BackendInfo",
    # Registry
    "StorageBackendRegistry",
    # Backup/Restore/Verify
    "backup_openpine",
    "restore_openpine",
    "verify_openpine",
]
