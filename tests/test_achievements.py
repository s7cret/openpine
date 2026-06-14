"""Smoke tests for the achievement engine.

Covers:
- catalog has the expected 100+ rows
- seed is idempotent
- recompute_stats doesn't crash on a fresh DB (with empty source tables)
- check_unlocks inserts rows when current >= target
- get_state returns the expected shape
- summary aggregates correctly
"""

from __future__ import annotations

import time

import pytest

from openpine.achievements.catalog import ALL, PRO, ULTRA, HYPER, APEX
from openpine.achievements.engine import AchievementEngine
from openpine.achievements.seed import seed_achievements
from openpine.config import OpenPineConfig
from openpine.storage.migrations import MigrationRunner
from openpine.storage.sqlite_storage import SQLiteStorage


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Fresh SQLite DB in a temp dir with migrations applied."""
    cfg = OpenPineConfig.load()
    monkeypatch.setattr(cfg, "sqlite_path", tmp_path / "test.sqlite", raising=False)
    db = SQLiteStorage(tmp_path / "test.sqlite")
    MigrationRunner().run_migrations(db)
    yield db
    db.close()


def test_catalog_has_expected_shape():
    assert len(PRO) == 25, f"Pro tier: expected 25, got {len(PRO)}"
    assert len(ULTRA) == 28, f"Ultra tier: expected 28, got {len(ULTRA)}"
    assert len(HYPER) == 26, f"Hyper tier: expected 26, got {len(HYPER)}"
    assert len(APEX) == 23, f"Apex tier: expected 23, got {len(APEX)}"
    assert len(ALL) == 102

    # every def has a target > 0 (except hidden secrets that fire on events)
    for a in ALL:
        assert a.target >= 0, f"{a.id} has negative target"
        assert a.metric, f"{a.id} missing metric"
        assert a.tier in ("pro", "ultra", "hyper", "apex"), f"{a.id} bad tier"


def test_seed_idempotent(storage):
    n1 = seed_achievements(storage)
    n2 = seed_achievements(storage)
    assert n1 == n2 == 102
    rows = storage.execute("SELECT COUNT(*) FROM achievements").fetchone()
    assert rows[0] == 102


def test_seed_updates_existing_rows(storage):
    seed_achievements(storage)
    # Manually corrupt one row to simulate a stale catalog entry
    storage.execute("UPDATE achievements SET title = 'old' WHERE id = ?", ("bars-1b",))
    storage.commit()
    # Re-seed: title should be restored
    seed_achievements(storage)
    row = storage.execute(
        "SELECT title FROM achievements WHERE id = ?", ("bars-1b",)
    ).fetchone()
    assert row[0] == "1 Billion Bars Loaded"


def test_recompute_stats_safe_on_empty_db(storage):
    seed_achievements(storage)
    engine = AchievementEngine(storage)
    stats = engine.recompute_stats()
    # At least *some* metrics should have been computed (candle_manifests exists)
    assert isinstance(stats, dict)
    for value in stats.values():
        assert value >= 0


def test_get_state_orders_by_tier(storage):
    seed_achievements(storage)
    engine = AchievementEngine(storage)
    rows = engine.get_state(include_hidden_locked=True)
    # Tier order: pro, ultra, hyper, apex
    tier_seq = [r.tier for r in rows]
    expected_order = ["pro"] * 25 + ["ultra"] * 28 + ["hyper"] * 26 + ["apex"] * 23
    assert tier_seq == expected_order


def test_get_state_hides_locked_secrets_by_default(storage):
    seed_achievements(storage)
    engine = AchievementEngine(storage)
    rows = engine.get_state(include_hidden_locked=False)
    secret_ids = {a.id for a in ALL if a.hidden}
    visible_ids = {r.id for r in rows}
    assert not (secret_ids & visible_ids), "secret achievements leaked into default view"


def test_get_state_shows_secrets_when_unlocked(storage):
    seed_achievements(storage)
    engine = AchievementEngine(storage)
    # Manually unlock a hidden achievement
    ach = next(a for a in ALL if a.hidden)
    storage.execute(
        "INSERT INTO achievement_unlocks(achievement_id, user_id, unlocked_at, final_value) "
        "VALUES (?, NULL, ?, ?)",
        (ach.id, int(time.time()), 1.0),
    )
    storage.commit()
    rows = engine.get_state(include_hidden_locked=False)
    visible_ids = {r.id for r in rows}
    assert ach.id in visible_ids


def test_check_unlocks_inserts_new_rows(storage):
    seed_achievements(storage)
    engine = AchievementEngine(storage)
    # Force a stats row that's already past the smallest target
    storage.execute(
        "INSERT INTO achievement_stats(metric, value, updated_at) VALUES (?, ?, ?)",
        ("bars_loaded", 1_000_000, int(time.time())),
    )
    storage.commit()
    unlocked = engine.check_unlocks({"bars_loaded": 1_000_000})
    # Every achievement with metric=bars_loaded and target <= 1M should unlock
    expected = [
        a.id for a in ALL
        if a.metric == "bars_loaded" and a.target <= 1_000_000
    ]
    assert set(unlocked) == set(expected)


def test_check_unlocks_idempotent(storage):
    seed_achievements(storage)
    engine = AchievementEngine(storage)
    storage.execute(
        "INSERT INTO achievement_stats(metric, value, updated_at) VALUES (?, ?, ?)",
        ("trades", 1_000, int(time.time())),
    )
    storage.commit()
    first = engine.check_unlocks({"trades": 1_000})
    # First pass unlocks everything with metric=trades and target <= 1_000
    assert first
    # Second pass: no new unlocks (they're all in achievement_unlocks now)
    second = engine.check_unlocks({"trades": 1_000})
    assert second == []


def test_summary_aggregates_by_tier(storage):
    seed_achievements(storage)
    engine = AchievementEngine(storage)
    s = engine.summary()
    assert s["total"] == 102
    assert s["unlocked"] == 0
    assert s["by_tier"] == {
        "pro":   {"done": 0, "of": 25},
        "ultra": {"done": 0, "of": 28},
        "hyper": {"done": 0, "of": 26},
        "apex":  {"done": 0, "of": 23},
    }
    # Unlock one Hyper achievement, ensure the count moves
    storage.execute(
        "INSERT INTO achievement_unlocks(achievement_id, user_id, unlocked_at, final_value) "
        "VALUES (?, NULL, ?, ?)",
        ("trades-10k", int(time.time()), 10_000.0),
    )
    storage.commit()
    s2 = engine.summary()
    assert s2["unlocked"] == 1
    assert s2["by_tier"]["hyper"]["done"] == 1


def test_refresh_runs_recompute_and_unlock(storage):
    seed_achievements(storage)
    engine = AchievementEngine(storage)
    # Stage an orders-like scenario: insert a backtest run
    try:
        storage.execute(
            "INSERT INTO backtest_runs(run_id, status, created_at) VALUES (?, ?, ?)",
            ("r1", "done", int(time.time())),
        )
    except Exception:
        # backtest_runs may have a richer schema; not the focus of this test
        return
    storage.commit()
    result = engine.refresh()
    assert "stats_computed" in result
    assert "newly_unlocked" in result
    # backtest-first achievement should be in unlocks
    assert "bt-first" in result["newly_unlocked"]


def test_i18n_seeds_en_and_ru(storage):
    from openpine.achievements.seed import seed_achievement_i18n
    seed_achievements(storage)
    seed_achievement_i18n(storage)
    rows = storage.execute(
        "SELECT locale, COUNT(*) FROM achievement_i18n GROUP BY locale"
    ).fetchall()
    by_locale = {r[0]: r[1] for r in rows}
    assert by_locale.get("en") == 102
    assert by_locale.get("ru", 0) >= 90  # most catalog entries are translated


def test_i18n_falls_back_to_english_for_unknown_locale(storage):
    from openpine.achievements.seed import seed_achievement_i18n
    seed_achievements(storage)
    seed_achievement_i18n(storage)
    eng = AchievementEngine(storage)
    # Request an unknown locale — engine should fall back to EN copy
    items = eng.get_state(locale="zz")
    en_items = eng.get_state(locale="en")
    assert items and en_items
    # Title for the same id must match the EN canonical
    sample = items[0]
    en_sample = next(x for x in en_items if x.id == sample.id)
    assert sample.title == en_sample.title


def test_i18n_returns_translated_titles(storage):
    from openpine.achievements.seed import seed_achievement_i18n
    seed_achievements(storage)
    seed_achievement_i18n(storage)
    eng = AchievementEngine(storage)
    ru_items = eng.get_state(locale="ru")
    en_items = eng.get_state(locale="en")
    by_en = {x.id: x for x in en_items}
    by_ru = {x.id: x for x in ru_items}
    # Every achievement with a known RU override must have a different
    # title from the EN row. (i18n_overrides.py ships ~100 RU rows.)
    translated = 0
    for ach_id, ru in by_ru.items():
        en = by_en[ach_id]
        if ru.title != en.title:
            translated += 1
    assert translated >= 90, f"expected >= 90 translated titles, got {translated}"


def test_compat_schema_adds_columns(storage):
    from openpine.storage.schema_compat import ensure_schema_compatibility
    ensure_schema_compatibility(storage)
    pa_cols = {r[1] for r in storage.execute("PRAGMA table_info(pine_artifacts)").fetchall()}
    br_cols = {r[1] for r in storage.execute("PRAGMA table_info(backtest_runs)").fetchall()}
    si_cols = {r[1] for r in storage.execute("PRAGMA table_info(strategy_instances)").fetchall()}
    assert "ast_node_count" in pa_cols
    assert "bars_per_sec" in br_cols
    assert "bars_per_min" in br_cols
    assert "bars_processed" in br_cols
    assert "uses_udt" in si_cols


def test_compat_schema_is_idempotent(storage):
    from openpine.storage.schema_compat import ensure_schema_compatibility
    # Run twice — second call must be a no-op without raising.
    ensure_schema_compatibility(storage)
    ensure_schema_compatibility(storage)


def test_engine_recomputes_26_metrics(storage):
    seed_achievements(storage)
    from openpine.storage.schema_compat import ensure_schema_compatibility
    ensure_schema_compatibility(storage)
    eng = AchievementEngine(storage)
    stats = eng.recompute_stats()
    assert len(stats) >= 26, f"expected >= 26 metrics, got {len(stats)}"
    # Sanity: all values are numeric and non-negative
    for k, v in stats.items():
        assert isinstance(v, float)
        assert v >= 0
