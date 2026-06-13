# Database

OpenPine uses SQLite for local product state. Runtime market-data lake details remain delegated to `marketdata-provider` where possible.

## Migration Contract

Migrations are numbered SQL files in `openpine/storage/migrations/` and are applied by `MigrationRunner` in order. The `schema_migrations` table records version, name, checksum, and applied timestamp.

## 4.0 Metadata Table

Migration `010_schema_metadata.sql` adds:

```sql
openpine_schema_metadata(key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL)
```

It stores:

- `schema_contract = openpine.sqlite.v4`
- `release_version = 4.0.0`

This table is a lightweight compatibility marker for future migration tooling and support diagnostics.

## Storage Policy

- Persisted timestamps are UTC milliseconds unless a table explicitly says otherwise. The configurable OpenPine timezone affects only parsing/display boundaries, not stored values.
- Pickled state is trusted-local only and gated by `OPENPINE_ALLOW_PICKLE_STATE=1`.
- Backtest time-series artifacts remain Parquet-backed; `pandas` and `pyarrow` are explicit backend dependencies.

## 4.0.0 SQLite index and event-schema hardening

The backend schema uses `openpine.sqlite.v4` as its SQLite contract. Migration
`011_performance_indexes.sql` adds the production index profile for the main
read paths: Pine source/artifact lookup, job polling and lease recovery, order
and fill history, candle manifest range lookup, event replay, event consumers,
backtest run history, and data-lake manifests.

The `events` table is normalized at migration/runtime boundaries. Older paths
created `payload_json`/`created_at`, while EventBus-created databases used
`payload`/`timestamp_ms`/`durable`. The compatibility layer keeps both column
families available and backfills values in both directions so durable events
remain readable after upgrades.

Use the storage health command before a deployment:

```bash
openpine storage migrate --path ./openpine.sqlite
openpine storage health --path ./openpine.sqlite
```

A healthy database reports `openpine.sqlite.v4`, no pending migrations, all
required indexes present, and an event schema marked as compatible.
