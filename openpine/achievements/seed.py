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
from openpine.achievements.i18n_overrides import ACHIEVEMENT_I18N
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
                1 if a.inverted else 0,
                now,
                now,
            )
        )
        sort += 1

    storage.execute_many(
        """
        INSERT OR REPLACE INTO achievements(
            id, tier, icon, title, description, target_value, metric,
            reward, hidden, sort_order, inverted, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        cast(Any, rows),
    )
    storage.commit()
    log.info("achievements_seeded", count=len(rows))
    return len(rows)


def seed_achievement_i18n(storage: SQLiteStorage) -> int:
    """Insert or refresh per-locale copy overrides.

    Behavior:
    - First, ensure every (achievement_id, locale='en') row exists with
      the canonical EN copy from the catalog. This way the engine can
      always JOIN against the i18n table for EN without falling back.
    - Then, apply the per-locale overrides from i18n_overrides.py via
      INSERT OR REPLACE. Missing keys keep the canonical EN copy.
    """
    # 1) EN rows for every achievement
    en_rows: list[tuple[Any, ...]] = []
    for a in ALL:
        en_rows.append((a.id, "en", a.title, a.description, a.reward))
    storage.execute_many(
        """
        INSERT OR REPLACE INTO achievement_i18n(
            achievement_id, locale, title, description, reward
        ) VALUES (?, ?, ?, ?, ?)
        """,
        cast(Any, en_rows),
    )
    # 2) Locale overrides
    if ACHIEVEMENT_I18N:
        override_rows: list[tuple[Any, ...]] = []
        for ach_id, locale, title, descr, reward in ACHIEVEMENT_I18N:
            override_rows.append((ach_id, locale, title, descr, reward))
        storage.execute_many(
            """
            INSERT OR REPLACE INTO achievement_i18n(
                achievement_id, locale, title, description, reward
            ) VALUES (?, ?, ?, ?, ?)
            """,
            cast(Any, override_rows),
        )
    storage.commit()
    log.info(
        "achievement_i18n_seeded",
        en_rows=len(en_rows),
        overrides=len(ACHIEVEMENT_I18N),
    )
    return len(en_rows) + len(ACHIEVEMENT_I18N)
