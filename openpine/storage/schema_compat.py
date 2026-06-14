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


ACHIEVEMENTS_COMPAT_COLUMNS: dict[str, tuple[ColumnSpec, ...]] = {
    # pine_artifacts: ast_node_count is written by the pine2ast compile
    # pipeline on every successful parse. The default 0 is fine for old
    # rows — the achievement engine's SUM just skips them.
    "pine_artifacts": (
        ColumnSpec("ast_node_count", "INTEGER NOT NULL DEFAULT 0"),
    ),
    # backtest_runs: throughput metrics. The compile pipeline measures
    # bars/sec and bars/min over the run window; if absent we fall back
    # to MAX(0) in the achievement engine.
    "backtest_runs": (
        ColumnSpec("bars_per_sec", "REAL NOT NULL DEFAULT 0"),
        ColumnSpec("bars_per_min", "REAL NOT NULL DEFAULT 0"),
        ColumnSpec("bars_processed", "INTEGER NOT NULL DEFAULT 0"),
    ),
    # strategy_instances: UDT detection. The compile pipeline flags
    # whether the strategy used user-defined types. Default 0.
    "strategy_instances": (
        ColumnSpec("uses_udt", "INTEGER NOT NULL DEFAULT 0"),
    ),
    # orders: side direction tracking. We don't add a new column —
    # the achievement engine derives ``both_sides`` from
    # COUNT(DISTINCT side) WHERE side IN ('buy','sell').
}


def ensure_achievements_compat_schema(storage: SQLiteStorage) -> None:
    """Add the columns the achievement engine reads but the
    core schema migrations don't yet ship with.

    Idempotent: add_missing_columns is a no-op for already-present
    columns. Safe to call on every gateway startup.
    """
    for table, specs in ACHIEVEMENTS_COMPAT_COLUMNS.items():
        try:
            add_missing_columns(storage, table, specs)
        except Exception:
            # If the table itself doesn't exist yet (early install
            # state), the migration runner will create it on next
            # boot. Nothing to do here.
            continue
    storage.commit()


def ensure_schema_compatibility(storage: SQLiteStorage) -> None:
    """Apply idempotent schema compatibility normalizers."""

    ensure_events_compat_schema(storage)
    ensure_achievements_compat_schema(storage)


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


def row_dict(row: tuple[Any, ...], columns: tuple[str, ...]) -> dict[str, Any]:
    """Map a SQLite row tuple to a dict for tests and diagnostics."""

    return dict(zip(columns, row))
