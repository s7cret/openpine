-- 012_achievements_schema.sql
-- v4 achievements: catalog, derived stats, unlock log.
-- All three tables are app-private; no FK to user-visible data so the
-- achievement engine can rebuild stats without breaking referential integrity.

-- Catalog: full list of achievements (106 + secret). Idempotent re-seed
-- is supported via INSERT OR REPLACE in Python.
CREATE TABLE IF NOT EXISTS achievements (
    id TEXT PRIMARY KEY,
    tier TEXT NOT NULL,
    icon TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    target_value REAL NOT NULL,
    metric TEXT NOT NULL,
    reward TEXT NOT NULL,
    hidden INTEGER NOT NULL DEFAULT 0,
    sort_order INTEGER NOT NULL DEFAULT 0,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_achievements_tier_sort
    ON achievements(tier, sort_order, id);
CREATE INDEX IF NOT EXISTS idx_achievements_metric
    ON achievements(metric);

-- Derived stats: one row per metric, value is the live counter we
-- maintain from event hooks and rebuild on every periodic recompute.
CREATE TABLE IF NOT EXISTS achievement_stats (
    metric TEXT PRIMARY KEY,
    value REAL NOT NULL DEFAULT 0,
    last_event_at INTEGER,
    updated_at INTEGER NOT NULL
);

-- Unlock log: append-only. One row per (achievement, user) the first
-- time the target is met. user_id is nullable today (single-user install);
-- reserved for future multi-tenant installations.
CREATE TABLE IF NOT EXISTS achievement_unlocks (
    achievement_id TEXT NOT NULL,
    user_id TEXT,
    unlocked_at INTEGER NOT NULL,
    final_value REAL NOT NULL,
    PRIMARY KEY (achievement_id, user_id),
    FOREIGN KEY (achievement_id) REFERENCES achievements(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_achievement_unlocks_user_time
    ON achievement_unlocks(user_id, unlocked_at DESC);
