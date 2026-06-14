-- 013_achievements_i18n.sql
-- Per-locale strings for the achievement catalog.
--
-- The achievements table is the source of truth for *what* an
-- achievement is (id, tier, metric, target). The i18n table is the
-- source of truth for *how* it is presented to the user (title,
-- description, reward). Without this split we can't:
--   - change copy without a code deploy
--   - add a new locale without touching the engine
--   - support per-achievement overrides (e.g. different framing
--     for the same achievement across locales)
--
-- On insert, the seed script copies the EN copy from the catalog
-- (idempotent INSERT OR REPLACE), then layers locale overrides
-- defined in openpine/achievements/i18n_overrides.py.

CREATE TABLE IF NOT EXISTS achievement_i18n (
    achievement_id TEXT NOT NULL,
    locale TEXT NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    reward TEXT NOT NULL,
    PRIMARY KEY (achievement_id, locale),
    FOREIGN KEY (achievement_id) REFERENCES achievements(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_achievement_i18n_locale
    ON achievement_i18n(locale, achievement_id);

-- 013_ast_node_count.sql (also in this migration file so a single
-- 013 upgrade is enough; the two changes are independent and shipped
-- together for convenience but live in separate logical concerns).
--
-- Adds ast_node_count to pine_artifacts. The compile pipeline (in
-- pine2ast) writes the per-artifact node count on every successful
-- parse; the achievement engine sums it across the catalog. Old
-- rows default to 0 so the achievement engine sees a graceful value
-- until the next compile rewrites the row.
--
-- ALTER TABLE uses IF NOT EXISTS-equivalent via the schema-compat
-- helper in storage/schema_compat.py (idempotent at runtime). We
-- declare the column here for documentation; the actual ALTER runs
-- from openpine/achievements/schema_compat.py on every gateway
-- startup so newly-deployed columns show up on existing databases
-- without re-running the migration runner.
