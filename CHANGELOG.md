# Changelog

- Backend coverage gate raised to 72% with CLI Pine artifact/state/plugin coverage, runtime adapter coverage, Telegram bot handler edge coverage, gateway lifespan coverage, and optimizer dry-run route coverage.
- Backend coverage gate raised to 70% with JobScheduler lifecycle/locking coverage, dashboard persistent-job/health coverage, export writer edge coverage, and a real export-directory creation fix.
## 4.0.0
- Backend coverage gate raised to 90% with large CLI strategy/data lifecycle, marketdata provider-adapter, exchange-metadata, stream-adapter, TV corpus, and compare-helper coverage.

- Backend coverage gate raised again to 65% with batch-runner metadata, live-runner mini-backtest/resume paths, CLI data helpers, plot export, dashboard/strategy route edges, and Telegram parsing coverage.

- Backend coverage gate raised to 65% with large gateway route, execution, strategy, websocket, Pine source, dashboard, and storage edge coverage.

- Added configurable default timezone handling for CLI/API date parsing (`timezone` / `OPENPINE_TIMEZONE`), replacing the hard-coded MSK offset while preserving the default UTC+03:00 behavior.

- Moved backend Python code into a normal `openpine/` package directory while leaving `openpine-ui/` untouched.
- Updated stack dependencies to the `v4.0.0` package family.
- Added `python -m openpine` entrypoint.
- Added release, distribution, and quality gates.
- Added deterministic source archive builder.
- Added SQLite schema metadata migration `010_schema_metadata.sql`.
- Added DB health/introspection helper.
- Made Binance spot quantity-step metadata deterministic/offline-safe for common symbols while retaining cache/API refresh support.
- Updated marketdata-provider version boundary to `4.0.0`.
- Relaxed visual-only runtime-contract Pine diagnostics so plots can pass through compile orchestration; unsupported `request.*` calls still fail closed in production.
- Formatted backend code with `black` and fixed `ruff` issues.

## 2.17.0

Legacy pre-4.0 OpenPine layout and stack versions.

### Storage schema hardening

- Added migration `011_performance_indexes.sql` with the v4 production SQLite
  index profile.
- Added idempotent event-schema compatibility normalizers for legacy and
  EventBus-created `events` table shapes.
- Added `openpine storage health` to check migrations, required indexes, schema
  contract metadata, and durable-event compatibility.
- Fixed Pine source artifact cleanup queries to use actual schema columns
  (`compile_artifacts.source_id`, `pine_artifacts.pine_id`).
