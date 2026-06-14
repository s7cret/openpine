-- 015_achievement_events.sql
-- Append-only event log for source-of-truth metrics that don't have
-- a natural SQL source. Achievement engine counts rows by event_type.
--
-- Usage (from any subsystem):
--   INSERT INTO achievement_events(event_id, event_type, source_id,
--                                  value, payload_json, created_at)
--   VALUES (?, ?, ?, ?, ?, ?)
--
-- The engine reads them via SELECT COUNT(*) or MAX(value) grouped by
-- event_type, so partial events are still meaningful.

CREATE TABLE IF NOT EXISTS achievement_events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,        -- e.g. 'ruin_recovery', 'shipped_lib'
    source_id TEXT,                  -- optional ref (run_id, lib_name, ...)
    value REAL NOT NULL DEFAULT 1.0, -- for MAX-style metrics; default 1
    payload_json TEXT,
    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_achievement_events_type_time
    ON achievement_events(event_type, created_at DESC);
