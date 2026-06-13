"""SQLite storage layer for OpenPine."""

from __future__ import annotations

import contextlib
import sqlite3
from pathlib import Path
from typing import Any, Iterator


class SQLiteStorage:
    """SQLite storage with WAL mode and transaction support."""

    def __init__(self, path: Path | None = None, busy_timeout_ms: int = 30_000) -> None:
        """Open SQLite database at path with WAL mode."""
        if path is None:
            from openpine.config import OpenPineConfig

            path = OpenPineConfig.load().sqlite_path
        self.path = Path(path)
        self.busy_timeout_ms = busy_timeout_ms
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: sqlite3.Connection | None = None
        self._open()

    def _open(self) -> None:
        """Open connection with WAL mode."""
        self._conn = sqlite3.connect(
            str(self.path),
            check_same_thread=False,
            timeout=self.busy_timeout_ms / 1000,
        )
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self._conn.execute("PRAGMA synchronous=NORMAL")
        self._conn.execute("PRAGMA temp_store=MEMORY")
        self._conn.execute(f"PRAGMA busy_timeout={self.busy_timeout_ms}")

    @property
    def conn(self) -> sqlite3.Connection:
        """Get the underlying connection."""
        if self._conn is None:
            raise RuntimeError("Storage is closed")
        return self._conn

    def execute(
        self, sql: str, params: tuple[Any, ...] | dict[str, Any] = ()
    ) -> sqlite3.Cursor:
        """Execute a single query."""
        return self.conn.execute(sql, params)

    def execute_many(
        self, sql: str, params_list: list[tuple[Any, ...] | dict[str, Any]]
    ) -> sqlite3.Cursor:
        """Execute a query with many parameter sets."""
        return self.conn.executemany(sql, params_list)

    def execute_script(self, sql: str) -> sqlite3.Cursor:
        """Execute a SQLite script atomically through the sqlite3 parser."""
        return self.conn.executescript(sql)

    def optimize(self) -> None:
        """Run SQLite lightweight query-planner maintenance."""
        self.conn.execute("PRAGMA optimize")

    def commit(self) -> None:
        """Commit the current transaction."""
        self.conn.commit()

    def rollback(self) -> None:
        """Rollback the current transaction."""
        self.conn.rollback()

    @contextlib.contextmanager
    def transaction(self) -> Iterator[None]:
        """Context manager for atomic operations."""
        try:
            yield
            self.commit()
        except Exception:
            self.rollback()
            raise

    def close(self) -> None:
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def __enter__(self) -> "SQLiteStorage":
        return self

    def __exit__(self, *args: Any) -> None:
        self.close()

    def __del__(self) -> None:
        # SQLite connections otherwise emit ResourceWarning when callers abandon a
        # short-lived storage wrapper during command/test teardown. close() is
        # idempotent, so the finalizer is safe for already-closed instances.
        self.close()
