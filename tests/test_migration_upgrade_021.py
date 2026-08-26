from __future__ import annotations

from openpine.storage import MigrationRunner, SQLiteStorage


def test_migration_021_creates_durable_strategy_execution_epochs(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    try:
        MigrationRunner().run_migrations(storage)
        columns = {
            row[1]
            for row in storage.execute(
                "PRAGMA table_info(strategy_execution_epochs)"
            ).fetchall()
        }
        applied = {
            row[0]
            for row in storage.execute(
                "SELECT version FROM schema_migrations"
            ).fetchall()
        }
    finally:
        storage.close()

    assert {"strategy_id", "mode", "started_at"} <= columns
    assert "021" in applied
