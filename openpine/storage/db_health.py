"""SQLite schema health/introspection helpers."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openpine.storage.migrations import _get_migration_files
from openpine.storage.schema_compat import table_columns
from openpine.storage.schema_indexes import REQUIRED_INDEXES
from openpine.storage.sqlite_storage import SQLiteStorage


@dataclass(frozen=True)
class SchemaHealth:
    """Release-oriented SQLite schema health report."""

    applied_versions: tuple[str, ...]
    pending_versions: tuple[str, ...]
    table_count: int
    index_count: int
    missing_indexes: tuple[str, ...]
    schema_contract: str | None
    event_schema_compatible: bool
    ok: bool


def _metadata_value(storage: SQLiteStorage, key: str) -> str | None:
    tables = storage.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='openpine_schema_metadata'"
    ).fetchall()
    if not tables:
        return None
    row = storage.execute(
        "SELECT value FROM openpine_schema_metadata WHERE key = ?", (key,)
    ).fetchone()
    return None if row is None else str(row[0])


def _existing_indexes(storage: SQLiteStorage) -> set[str]:
    rows = storage.execute("SELECT name FROM sqlite_master WHERE type='index'").fetchall()
    return {str(row[0]) for row in rows}


def schema_health(storage: SQLiteStorage) -> SchemaHealth:
    """Return migration/index/event compatibility health for a SQLite DB."""

    rows = storage.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    applied = tuple(str(row[0]) for row in rows)
    expected = tuple(
        f"{order:03d}"
        for order, _, _ in _get_migration_files(Path(__file__).parent / "migrations")
    )
    pending = tuple(version for version in expected if version not in applied)
    table_count = int(
        storage.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table'").fetchone()[0]
    )
    existing_indexes = _existing_indexes(storage)
    missing_indexes = tuple(
        index.name for index in REQUIRED_INDEXES if index.name not in existing_indexes
    )
    event_columns = table_columns(storage, "events")
    event_schema_compatible = {
        "event_id",
        "event_type",
        "payload_json",
        "payload",
        "created_at",
        "timestamp_ms",
        "durable",
    }.issubset(event_columns)
    contract = _metadata_value(storage, "schema_contract")
    ok = not pending and not missing_indexes and event_schema_compatible
    return SchemaHealth(
        applied_versions=applied,
        pending_versions=pending,
        table_count=table_count,
        index_count=len(existing_indexes),
        missing_indexes=missing_indexes,
        schema_contract=contract,
        event_schema_compatible=event_schema_compatible,
        ok=ok,
    )
