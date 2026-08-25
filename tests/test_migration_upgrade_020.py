from __future__ import annotations

from openpine.storage.migrations import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage


def test_migration_020_records_preexisting_semantic_profile_column(tmp_path) -> None:
    storage = SQLiteStorage(tmp_path / "openpine.sqlite")
    try:
        MigrationRunner().run_migrations(storage)
        storage.execute(
            "DELETE FROM schema_migrations WHERE version = ?",
            ("020",),
        )
        storage.commit()

        applied = MigrationRunner().run_migrations(storage)

        columns = [
            str(row[1])
            for row in storage.execute("PRAGMA table_info(strategy_instances)").fetchall()
        ]
        row = storage.execute(
            "SELECT version, name FROM schema_migrations WHERE version = ?",
            ("020",),
        ).fetchone()
        assert applied == ["strategy_semantic_profile"]
        assert columns.count("semantic_profile") == 1
        assert row == ("020", "strategy_semantic_profile")
    finally:
        storage.close()
