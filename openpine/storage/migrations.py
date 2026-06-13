"""Migration runner for OpenPine SQLite schema."""

from __future__ import annotations

import hashlib
import re
import time
from pathlib import Path

from openpine.storage.schema_compat import ensure_schema_compatibility
from openpine.storage.sqlite_storage import SQLiteStorage

_MIGRATION_FILE_RE = re.compile(r"^(\d+)_([^.]+)\.sql$")


def _get_migration_files(migrations_dir: Path) -> list[tuple[int, str, Path]]:
    """Return sorted list of ``(order, name, path)`` for SQL migration files."""

    files: list[tuple[int, str, Path]] = []
    if not migrations_dir.is_dir():
        return files
    for path in migrations_dir.iterdir():
        if not path.is_file() or path.suffix != ".sql":
            continue
        match = _MIGRATION_FILE_RE.match(path.name)
        if match:
            files.append((int(match.group(1)), match.group(2), path))
    files.sort(key=lambda item: item[0])
    return files


class MigrationRunner:
    """Runs numbered SQL migrations on a SQLiteStorage instance."""

    def run_migrations(self, storage: SQLiteStorage) -> list[str]:
        """Apply pending migrations and return migration names applied."""

        migrations_applied: list[str] = []

        storage.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                applied_at INTEGER NOT NULL,
                description TEXT NOT NULL,
                checksum TEXT NOT NULL,
                id INTEGER NOT NULL UNIQUE,
                name TEXT NOT NULL UNIQUE
            )
            """
        )
        storage.commit()

        # Compatibility normalizers run before SQL migrations because old
        # EventBus-created databases can already contain an ``events`` table
        # missing the legacy ``created_at``/``payload_json`` columns used by
        # migration 011 indexes.
        ensure_schema_compatibility(storage)

        applied_rows = storage.execute("SELECT version, name FROM schema_migrations").fetchall()
        applied_versions = {str(row[0]) for row in applied_rows}
        applied_names = {str(row[1]) for row in applied_rows}

        migrations_dir = Path(__file__).parent / "migrations"
        migration_files = _get_migration_files(migrations_dir)

        for order, name, path in migration_files:
            version = f"{order:03d}"
            if version in applied_versions or name in applied_names:
                continue

            sql = path.read_text(encoding="utf-8")
            now = int(time.time())
            checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
            description = name.replace("_", " ")

            with storage.transaction():
                # Use the SQLite script parser rather than ``sql.split(';')``;
                # splitting breaks triggers, CHECK expressions and string values
                # containing semicolons.
                storage.execute_script(sql)
                storage.execute(
                    """
                    INSERT INTO schema_migrations
                      (version, applied_at, description, checksum, id, name)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (version, now, description, checksum, order, name),
                )

            migrations_applied.append(name)
            applied_versions.add(version)
            applied_names.add(name)

        ensure_schema_compatibility(storage)
        storage.optimize()
        storage.commit()
        return migrations_applied
