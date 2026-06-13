-- Migration 006: Add compaction support to candle_manifests

ALTER TABLE candle_manifests ADD COLUMN is_active INTEGER DEFAULT 1;
ALTER TABLE candle_manifests ADD COLUMN superseded_by TEXT NULL;
