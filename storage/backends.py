"""Storage backend registry — discovers and reports on all configured backends."""

from __future__ import annotations

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


class StorageBackendRegistry:
    """Discovers and reports on all OpenPine storage backends."""

    def __init__(self) -> None:
        self._backends: list[StorageBackend] = [
            SQLiteControlStorageAdapter(),
            ParquetDataLakeAdapter(),
            DuckDBAnalyticsAdapter(),
            PostgresControlStorageAdapter(),
        ]

    def list_backends(self) -> list[StorageBackend]:
        """All registered storage backends."""
        return list(self._backends)

    def get_by_role(self, role: BackendRole) -> list[StorageBackend]:
        """All backends that fulfil a given role."""
        return [b for b in self._backends if b.role == role]

    def get_by_name(self, name: str) -> StorageBackend | None:
        """Backend by short name, or None."""
        for b in self._backends:
            if b.name == name:
                return b
        return None

    def check_all(self) -> list[BackendInfo]:
        """Run health check on all backends, return summary list."""
        return [b.health_check() for b in self._backends]

    def summary_table(self) -> list[dict]:
        """Human-readable summary rows for all backends."""
        rows = []
        for info in self.check_all():
            rows.append({
                "name": info.name,
                "role": info.role.value,
                "health": info.health.value,
                "version": info.version or "-",
                "error": info.error or "-",
            })
        return rows
