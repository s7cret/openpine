"""PineSourceRegistry — in-memory + SQLite persistence for Pine sources."""

from __future__ import annotations

import hashlib
import sqlite3
import time
from pathlib import Path
from typing import Protocol

from openpine.config import DEFAULT_CONFIG
from openpine.pine.source import PineSource


class PineSourceRegistry(Protocol):
    """Protocol for Pine source registry — section 7.1."""

    def add_source(self, source_text: str, name: str) -> PineSource:
        """Add a Pine source. Returns the created PineSource."""
        ...

    def get_source(self, pine_ref: str) -> PineSource:
        """Get a Pine source by id or name (pine_ref)."""
        ...

    def list_sources(self) -> list[PineSource]:
        """List all Pine sources."""
        ...

    def remove_source(self, pine_ref: str) -> None:
        """Remove a Pine source by id or name."""
        ...


def _make_source_id(source_text: str) -> str:
    """Derive a stable source id from content hash + timestamp."""
    ts = str(int(time.time() * 1000))
    h = hashlib.sha256(source_text.encode()).hexdigest()[:16]
    return f"pine_{h}_{ts}"


def _source_from_row(row: tuple) -> PineSource:
    return PineSource(
        id=row[0],
        name=row[2],
        source_text=row[5],
        source_path=row[3],
        source_hash=row[4],
        version=row[6],
        source_type=row[7],
        active_artifact_id=row[8],
        created_at=row[9],
        updated_at=row[10],
    )


class SQLitePineSourceRegistry:
    """PineSourceRegistry backed by in-memory dict + SQLite persistence."""

    def __init__(self, db_path: Path | None = None) -> None:
        if db_path is None:
            db_path = DEFAULT_CONFIG.sqlite_path
        self._db_path = db_path
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._mem: dict[str, PineSource] = {}
        self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS pine_sources (
                id TEXT PRIMARY KEY,
                pine_id TEXT UNIQUE,
                name TEXT NOT NULL UNIQUE,
                source_path TEXT,
                source_hash TEXT,
                source_text TEXT NOT NULL,
                version TEXT NOT NULL DEFAULT '1.0.0',
                source_type TEXT NOT NULL DEFAULT 'strategy',
                active_artifact_id TEXT,
                created_at INTEGER NOT NULL,
                updated_at INTEGER NOT NULL
            )
        """)
        self._conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_pine_sources_name ON pine_sources(name)
        """)
        self._conn.commit()
        # Load existing rows into memory
        rows = self._conn.execute("SELECT id FROM pine_sources").fetchall()
        for (row_id,) in rows:
            row = self._conn.execute(
                "SELECT * FROM pine_sources WHERE id = ?", (row_id,)
            ).fetchone()
            if row:
                self._mem[row_id] = _source_from_row(row)

    def add_source(self, source_text: str, name: str) -> PineSource:
        """Add a new Pine source."""
        source_id = _make_source_id(source_text)
        now = int(time.time() * 1000)
        source = PineSource(
            id=source_id,
            name=name,
            source_text=source_text,
            version="1.0.0",
            source_type="strategy",
            created_at=now,
            updated_at=now,
        )
        self._conn.execute(
            """INSERT INTO pine_sources
               (id, pine_id, name, source_text, version, source_type, active_artifact_id, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                source.id,
                None,
                source.name,
                source.source_text,
                source.version,
                source.source_type,
                source.active_artifact_id,
                source.created_at,
                source.updated_at,
            ),
        )
        self._conn.commit()
        self._mem[source.id] = source
        return source

    def get_source(self, pine_ref: str) -> PineSource:
        """Get a Pine source by id or name."""
        # Try by id first
        if pine_ref in self._mem:
            return self._mem[pine_ref]
        # Fall back to name lookup
        for src in self._mem.values():
            if src.name == pine_ref:
                return src
        raise KeyError(f"PineSource not found: {pine_ref!r}")

    def list_sources(self) -> list[PineSource]:
        """List all Pine sources."""
        return list(self._mem.values())

    def remove_source(self, pine_ref: str) -> None:
        """Remove a Pine source by id or name."""
        source = self.get_source(pine_ref)
        self._conn.execute("DELETE FROM pine_sources WHERE id = ?", (source.id,))
        self._conn.commit()
        del self._mem[source.id]

    def set_active_artifact(self, pine_ref: str, artifact_id: str) -> None:
        """Set the active artifact for a Pine source."""
        source = self.get_source(pine_ref)
        source.active_artifact_id = artifact_id
        source.updated_at = int(time.time() * 1000)
        self._conn.execute(
            "UPDATE pine_sources SET active_artifact_id = ?, updated_at = ? WHERE id = ?",
            (artifact_id, source.updated_at, source.id),
        )
        self._conn.commit()
        self._mem[source.id] = source

    def close(self) -> None:
        """Close the database connection."""
        self._conn.close()
