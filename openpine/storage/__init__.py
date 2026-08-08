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
from openpine.storage.backtest_dto import (
    ARTIFACT_TYPE_BAR_OUTPUTS,
    ARTIFACT_TYPE_EQUITY_CURVE,
    ARTIFACT_TYPE_PLOT_OUTPUTS,
    ARTIFACT_TYPE_REPORT_JSON,
    ARTIFACT_TYPE_REPORT_MD,
    ARTIFACT_TYPE_TRADES,
    BacktestArtifact,
    BacktestMetricsSummary,
    BacktestRun,
    BacktestRunRequest,
    BacktestTrade,
)
from openpine.storage.backtest_storage import BacktestResultStore
from openpine.storage.backup import backup_openpine, restore_openpine, verify_openpine
from openpine.storage.manifests import ManifestStore
from openpine.storage.migrations import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage
from openpine.storage.strategy_ledger import (
    LedgerSource,
    PositionSide,
    StrategyLedger,
    StrategyPosition,
    StrategyTrade,
    TradeStatus,
)

__all__ = [
    "ARTIFACT_TYPE_BAR_OUTPUTS",
    "ARTIFACT_TYPE_EQUITY_CURVE",
    "ARTIFACT_TYPE_PLOT_OUTPUTS",
    "ARTIFACT_TYPE_REPORT_JSON",
    "ARTIFACT_TYPE_REPORT_MD",
    "ARTIFACT_TYPE_TRADES",
    "BackendHealth",
    "BackendInfo",
    "BackendRole",
    "BacktestArtifact",
    "BacktestMetricsSummary",
    "BacktestResultStore",
    "BacktestRun",
    # DTOs
    "BacktestRunRequest",
    "BacktestTrade",
    "DuckDBAnalyticsAdapter",
    "LedgerSource",
    "ManifestStore",
    "MigrationRunner",
    "ParquetDataLakeAdapter",
    "PositionSide",
    "PostgresControlStorageAdapter",
    # Adapters
    "SQLiteControlStorageAdapter",
    # Core SQLite
    "SQLiteStorage",
    "StorageBackend",
    # Registry
    "StorageBackendRegistry",
    "StrategyLedger",
    "StrategyPosition",
    "StrategyTrade",
    "TradeStatus",
    # Backup/Restore/Verify
    "backup_openpine",
    "restore_openpine",
    "verify_openpine",
]
