"""Migration runner for OpenPine SQLite schema."""

from __future__ import annotations

import re
import hashlib
import time
from pathlib import Path

from openpine.storage.sqlite_storage import SQLiteStorage

_MIGRATION_FILE_RE = re.compile(r"^(\d+)_([^.]+)\.sql$")


def _get_migration_files(migrations_dir: Path) -> list[tuple[int, str, Path]]:
    """Return sorted list of (order, name, path) for SQL migration files."""
    files: list[tuple[int, str, Path]] = []
    if not migrations_dir.is_dir():
        return files
    for path in migrations_dir.iterdir():
        if path.is_file() and path.suffix == ".sql":
            m = _MIGRATION_FILE_RE.match(path.name)
            if m:
                order = int(m.group(1))
                name = m.group(2)
                files.append((order, name, path))
    files.sort(key=lambda x: x[0])
    return files


class MigrationRunner:
    """Runs numbered SQL migrations on a SQLiteStorage instance."""

    def run_migrations(self, storage: SQLiteStorage) -> list[str]:
        """Apply all pending migrations found in the migrations directory.

        Returns list of migration names that were applied.
        """
        migrations_applied: list[str] = []

        # Ensure schema_migrations table exists. The version/description/checksum
        # columns are the public contract; id/name remain for older local callers.
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

        # Determine which migrations are already applied
        cursor = storage.execute("SELECT version, name FROM schema_migrations")
        applied_rows = cursor.fetchall()
        applied_versions = {row[0] for row in applied_rows}
        applied_names = {row[1] for row in applied_rows}

        # Find migration files
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
                # Execute migration SQL (may include multiple statements)
                for statement in sql.split(";"):
                    statement = statement.strip()
                    if statement:
                        storage.execute(statement)

                # Record migration
                storage.execute(
                    """
                    INSERT INTO schema_migrations
                      (version, applied_at, description, checksum, id, name)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (version, now, description, checksum, order, name),
                )

            migrations_applied.append(name)

        return migrations_applied
