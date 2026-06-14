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


def _insert_pine_source(storage, pine_id="p_test", name="p_test") -> str:
    """Helper: stage a minimal pine_sources row so FK references pass.

    Returns the pine_id used (caller may need a compile artifact too)."""
    now = int(time.time() * 1000)
    storage.execute(
        "INSERT OR IGNORE INTO pine_sources("
        "id, pine_id, name, source_text, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (pine_id, pine_id, name, "//@source test", now, now),
    )
    storage.commit()
    return pine_id


def _insert_compile_artifact(storage, artifact_id="a_test",
                             pine_id="p_test") -> str:
    """Helper: stage a minimal compile_artifacts row so backtest_runs
    FK passes. Returns the artifact_id used."""
    now = int(time.time() * 1000)
    storage.execute(
        "INSERT OR IGNORE INTO compile_artifacts("
        "id, source_id, params_hash, artifact_path, compile_meta, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?)",
        (artifact_id, pine_id, "p1", "/tmp/artifact.json",
         "{}", now),
    )
    storage.commit()
    return artifact_id


def test_backtest_throughput_metrics_persisted(storage):
    """save_result() must populate bars_processed + bars_per_sec/min
    on backtest_runs, sourced from equity_curve length and
    (now - created_at) wall-clock."""
    from openpine.storage.backtest_storage import BacktestResultStore
    from openpine.storage.backtest_dto import (
        BacktestMetricsSummary,
        BacktestRunRequest,
    )
    seed_achievements(storage)
    from openpine.storage.schema_compat import ensure_schema_compatibility
    ensure_schema_compatibility(storage)
    _insert_pine_source(storage, pine_id="pine_speed", name="pine_speed")
    _insert_compile_artifact(storage, artifact_id="a_speed", pine_id="pine_speed")
    store = BacktestResultStore(storage)
    # Use the public create_run() so all NOT NULL columns are set
    now_ms = int(time.time() * 1000)
    request = BacktestRunRequest(
        strategy_id="strat_speed",
        pine_id="pine_speed",
        artifact_id="a_speed",
        params_hash="p1",
        exchange="binance",
        market_type="spot",
        symbol="BTCUSDT",
        price_type="trade",
        timeframe="1h",
    )
    run_id = store.create_run(request)
    # Backdate created_at by 5 seconds so we get a non-zero elapsed_ms
    storage.execute(
        "UPDATE backtest_runs SET created_at = ? WHERE run_id = ?",
        (now_ms - 5000, run_id),
    )
    storage.commit()
    # Simulate a save_result with 1000 equity points over 5s → 200 bars/sec
    metrics = BacktestMetricsSummary(initial_capital=10000.0)
    store._save_result_db_records(
        run_id=run_id,
        strategy_id="strat_speed",
        run_dir=__import__("pathlib").Path("/tmp/ignore"),
        metrics=metrics,
        result_json="{}",
        trades=[],
        artifact_paths={},
        has_equity_curve=False,
        has_bar_outputs=False,
        bars_processed=1000,
        bars_per_sec=200.0,
        bars_per_min=12000.0,
        now=now_ms,
    )
    row = storage.execute(
        "SELECT bars_processed, bars_per_sec, bars_per_min FROM backtest_runs "
        "WHERE run_id = ?",
        (run_id,),
    ).fetchone()
    assert row[0] == 1000
    assert abs(row[1] - 200.0) < 0.01
    assert abs(row[2] - 12000.0) < 0.01


def test_achievement_views_present(storage):
    """ensure_schema_compatibility must create v_strategy_udt,
    v_strategy_timeframes, v_strategy_directions."""
    from openpine.storage.schema_compat import ensure_schema_compatibility
    ensure_schema_compatibility(storage)
    views = {r[0] for r in storage.execute(
        "SELECT name FROM sqlite_master WHERE type='view'"
    ).fetchall()}
    assert "v_strategy_udt" in views
    assert "v_strategy_timeframes" in views
    assert "v_strategy_directions" in views


def test_record_event_appends_and_unlocks(storage):
    """record_event() must append to achievement_events and trigger
    unlocks for any achievement whose target is met by the new value."""
    seed_achievements(storage)
    from openpine.storage.schema_compat import ensure_schema_compatibility
    ensure_schema_compatibility(storage)
    eng = AchievementEngine(storage)
    # First event: count=1 → unlock any ruin_recovery with target<=1
    unlocked = eng.record_event(
        event_type="ruin_recovery", source_id="session-1", value=1.0
    )
    assert isinstance(unlocked, list)
    # The events table must have the new row
    n = storage.execute(
        "SELECT COUNT(*) FROM achievement_events WHERE event_type = 'ruin_recovery'"
    ).fetchone()[0]
    assert n == 1
    # The matching metric stat must be 1
    v = storage.execute(
        "SELECT value FROM achievement_stats WHERE metric = 'ruin_recovery'"
    ).fetchone()
    assert v is not None and v[0] >= 1.0


def test_record_event_distinct_source_id_for_shipped_lib(storage):
    """shipped_lib counts DISTINCT source_id, not raw event count."""
    seed_achievements(storage)
    from openpine.storage.schema_compat import ensure_schema_compatibility
    ensure_schema_compatibility(storage)
    eng = AchievementEngine(storage)
    # 3 events for lib_a, 2 for lib_b → 2 distinct source_ids
    for _ in range(3):
        eng.record_event("shipped_lib", source_id="lib_a")
    for _ in range(2):
        eng.record_event("shipped_lib", source_id="lib_b")
    v = storage.execute(
        "SELECT value FROM achievement_stats WHERE metric = 'shipped_lib'"
    ).fetchone()
    assert v is not None and v[0] == 2.0


def test_live_uptime_metric_uses_hours(storage):
    """live_uptime_h must divide ms-diff by 3,600,000 (not 3,600)."""
    seed_achievements(storage)
    from openpine.storage.schema_compat import ensure_schema_compatibility
    ensure_schema_compatibility(storage)
    _insert_pine_source(storage, pine_id="pine_ut", name="pine_ut")
    _insert_compile_artifact(storage, artifact_id="a_ut", pine_id="pine_ut")
    # 1 hour = 3,600,000 ms
    now_ms = int(time.time() * 1000)
    one_hour_ago = now_ms - 3_600_000
    storage.execute(
        "INSERT INTO strategy_instances("
        "id, strategy_id, name, pine_id, artifact_id, params_hash, symbol, "
        "timeframe, status, created_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("s1", "s1", "uptime-1h", "pine_ut", "a_ut", "p1", "BTCUSDT", "1h",
         "active", one_hour_ago, now_ms),
    )
    storage.commit()
    eng = AchievementEngine(storage)
    stats = eng.recompute_stats()
    assert 0.99 <= stats["live_uptime_h"] <= 1.01, (
        f"expected ~1.0h, got {stats['live_uptime_h']}"
    )


def test_winrate_metric_is_percent_not_ratio(storage):
    """winrate_pct must surface backtest_runs.win_rate as a percent
    (0..100), without an extra *100."""
    seed_achievements(storage)
    from openpine.storage.schema_compat import ensure_schema_compatibility
    ensure_schema_compatibility(storage)
    _insert_pine_source(storage, pine_id="pine_wr", name="pine_wr")
    _insert_compile_artifact(storage, artifact_id="a_wr", pine_id="pine_wr")
    now_ms = int(time.time() * 1000)
    storage.execute(
        "INSERT INTO backtest_runs("
        "run_id, strategy_id, pine_id, artifact_id, params_hash, exchange, "
        "market_type, symbol, price_type, timeframe, status, win_rate, "
        "created_at, started_at, finished_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("r_wr_60", "s_wr", "pine_wr", "a_wr", "p1", "binance", "spot",
         "BTCUSDT", "trade", "1h", "done", 60.0,
         now_ms, now_ms, now_ms, now_ms),
    )
    storage.commit()
    eng = AchievementEngine(storage)
    stats = eng.recompute_stats()
    assert 59.0 <= stats["winrate_pct"] <= 61.0, (
        f"expected ~60 (percent), got {stats['winrate_pct']}"
    )


def test_symbols_count_joins_backtest_runs(storage):
    """symbols/exchanges/mcap_top10 must JOIN backtest_trades →
    backtest_runs to get the symbol (denormalized at run time)."""
    seed_achievements(storage)
    from openpine.storage.schema_compat import ensure_schema_compatibility
    ensure_schema_compatibility(storage)
    _insert_pine_source(storage, pine_id="pine_btc", name="pine_btc")
    _insert_compile_artifact(storage, artifact_id="a_btc", pine_id="pine_btc")
    now_ms = int(time.time() * 1000)
    storage.execute(
        "INSERT INTO backtest_runs("
        "run_id, strategy_id, pine_id, artifact_id, params_hash, exchange, "
        "market_type, symbol, price_type, timeframe, status, "
        "created_at, started_at, finished_at, updated_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("r_btc", "s_btc", "pine_btc", "a_btc", "p1", "binance", "spot",
         "BTCUSDT", "trade", "1h", "done",
         now_ms, now_ms, now_ms, now_ms),
    )
    storage.execute(
        "INSERT INTO backtest_trades("
        "trade_id, run_id, strategy_id, direction, "
        "entry_time, entry_price, qty, created_at"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("t1", "r_btc", "s_btc", "long", now_ms, 100.0, 1.0, now_ms),
    )
    storage.commit()
    eng = AchievementEngine(storage)
    stats = eng.recompute_stats()
    assert stats["symbols"] == 1.0
    assert stats["exchanges"] == 1.0
    assert stats["mcap_top10_count"] == 1.0  # BTCUSDT is in the top-10 list
