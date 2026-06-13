-- OpenPine 4.0 schema metadata and release boundary marker.
CREATE TABLE IF NOT EXISTS openpine_schema_metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL,
    updated_at INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER))
);

INSERT OR REPLACE INTO openpine_schema_metadata (key, value, updated_at)
VALUES
    ('schema_contract', 'openpine.sqlite.v4', CAST(strftime('%s', 'now') AS INTEGER)),
    ('release_version', '4.0.0', CAST(strftime('%s', 'now') AS INTEGER));
