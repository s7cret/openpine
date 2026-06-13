"""Compatibility normalizers for OpenPine SQLite schemas."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from openpine.storage.sqlite_storage import SQLiteStorage


@dataclass(frozen=True)
class ColumnSpec:
    """Column definition used by compatibility migrations."""

    name: str
    ddl: str


EVENTS_COMPAT_COLUMNS: tuple[ColumnSpec, ...] = (
    ColumnSpec("aggregate_type", "TEXT"),
    ColumnSpec("aggregate_id", "TEXT"),
    ColumnSpec("payload_json", "TEXT"),
    ColumnSpec("status", "TEXT NOT NULL DEFAULT 'NEW'"),
    ColumnSpec("created_at", "INTEGER"),
    ColumnSpec("payload", "TEXT"),
    ColumnSpec("timestamp_ms", "INTEGER"),
    ColumnSpec("durable", "INTEGER NOT NULL DEFAULT 1"),
)


def _quote_identifier(identifier: str) -> str:
    if not identifier.replace("_", "").isalnum():
        raise ValueError(f"Unsafe SQLite identifier: {identifier!r}")
    return f'"{identifier}"'


def table_columns(storage: SQLiteStorage, table: str) -> set[str]:
    """Return the column names for a table, or an empty set if absent."""

    cursor = storage.execute(f"PRAGMA table_info({_quote_identifier(table)})")
    return {str(row[1]) for row in cursor.fetchall()}


def add_missing_columns(
    storage: SQLiteStorage,
    table: str,
    specs: tuple[ColumnSpec, ...],
) -> tuple[str, ...]:
    """Add missing columns and return their names."""

    added: list[str] = []
    existing = table_columns(storage, table)
    quoted_table = _quote_identifier(table)
    for spec in specs:
        if spec.name in existing:
            continue
        storage.execute(f"ALTER TABLE {quoted_table} ADD COLUMN {spec.name} {spec.ddl}")
        added.append(spec.name)
        existing.add(spec.name)
    return tuple(added)


def ensure_events_compat_schema(storage: SQLiteStorage) -> None:
    """Normalize legacy and EventBus-created ``events`` tables.

    Historical migrations created ``payload_json``/``created_at`` columns.  The
    runtime EventBus later created ``payload``/``timestamp_ms``/``durable``.  A
    database initialized by one path could therefore fail when used by the
    other.  The normalizer keeps both shapes available and backfills values in
    both directions without overwriting existing data.
    """

    storage.execute(
        """
        CREATE TABLE IF NOT EXISTS events (
            event_id TEXT PRIMARY KEY,
            event_type TEXT NOT NULL,
            aggregate_type TEXT,
            aggregate_id TEXT,
            payload_json TEXT,
            status TEXT NOT NULL DEFAULT 'NEW',
            created_at INTEGER,
            payload TEXT,
            timestamp_ms INTEGER,
            durable INTEGER NOT NULL DEFAULT 1
        )
        """
    )
    add_missing_columns(storage, "events", EVENTS_COMPAT_COLUMNS)
    storage.execute(
        "UPDATE events SET payload_json = payload WHERE payload_json IS NULL AND payload IS NOT NULL"
    )
    storage.execute(
        "UPDATE events SET payload = payload_json WHERE payload IS NULL AND payload_json IS NOT NULL"
    )
    storage.execute(
        "UPDATE events SET created_at = timestamp_ms WHERE created_at IS NULL AND timestamp_ms IS NOT NULL"
    )
    storage.execute(
        "UPDATE events SET timestamp_ms = created_at WHERE timestamp_ms IS NULL AND created_at IS NOT NULL"
    )
    storage.execute("UPDATE events SET durable = 1 WHERE durable IS NULL")
    storage.execute("UPDATE events SET status = 'NEW' WHERE status IS NULL")
    storage.execute("CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type, timestamp_ms)")
    storage.execute("CREATE INDEX IF NOT EXISTS idx_events_type_time ON events(event_type, created_at DESC)")
    storage.execute("CREATE INDEX IF NOT EXISTS idx_events_status_time ON events(status, created_at DESC)")
    storage.execute("CREATE INDEX IF NOT EXISTS idx_events_aggregate ON events(aggregate_type, aggregate_id, created_at DESC)")
    storage.commit()


def ensure_schema_compatibility(storage: SQLiteStorage) -> None:
    """Apply idempotent schema compatibility normalizers."""

    ensure_events_compat_schema(storage)


def row_dict(row: tuple[Any, ...], columns: tuple[str, ...]) -> dict[str, Any]:
    """Map a SQLite row tuple to a dict for tests and diagnostics."""

    return dict(zip(columns, row))
