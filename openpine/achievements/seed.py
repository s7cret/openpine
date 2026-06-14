"""Idempotent seed of the achievement catalog into SQLite.

Run on gateway startup. Safe to call repeatedly: each entry is keyed
by ``id`` and re-inserts use OR REPLACE so copy/icon/title updates
in ``catalog.py`` propagate without losing the existing unlock log.
"""

from __future__ import annotations

import time
from typing import Any, cast

from openpine._compat import structlog
from openpine.achievements.catalog import ALL
from openpine.storage.sqlite_storage import SQLiteStorage

log = structlog.get_logger(__name__)


def seed_achievements(storage: SQLiteStorage) -> int:
    """Insert or refresh catalog rows. Returns the number of rows touched."""
    now = int(time.time())
    sort = 0
    rows: list[tuple[Any, ...]] = []
    for a in ALL:
        rows.append(
            (
                a.id,
                a.tier,
                a.icon,
                a.title,
                a.description,
                float(a.target),
                a.metric,
                a.reward,
                1 if a.hidden else 0,
                sort,
                now,
                now,
            )
        )
        sort += 1

    storage.execute_many(
        """
        INSERT OR REPLACE INTO achievements(
            id, tier, icon, title, description, target_value, metric,
            reward, hidden, sort_order, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        cast(Any, rows),
    )
    storage.commit()
    log.info("achievements_seeded", count=len(rows))
    return len(rows)
