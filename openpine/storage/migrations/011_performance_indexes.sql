-- 011_performance_indexes.sql
-- v4 backend index profile and schema compatibility metadata.

CREATE INDEX IF NOT EXISTS idx_pine_sources_hash
    ON pine_sources(source_hash);
CREATE INDEX IF NOT EXISTS idx_pine_sources_active_artifact
    ON pine_sources(active_artifact_id, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_pine_artifacts_pine_created
    ON pine_artifacts(pine_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_pine_artifacts_hash_versions
    ON pine_artifacts(source_hash, pine2ast_version, ast2python_version, pinelib_contract_version);

CREATE INDEX IF NOT EXISTS idx_compile_artifacts_source_created
    ON compile_artifacts(source_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_compile_artifacts_params_hash
    ON compile_artifacts(params_hash, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_strategy_instances_pine_id
    ON strategy_instances(pine_id);
CREATE INDEX IF NOT EXISTS idx_strategy_instances_artifact_id
    ON strategy_instances(artifact_id);
CREATE INDEX IF NOT EXISTS idx_strategy_instances_enabled_status
    ON strategy_instances(enabled, status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_instances_symbol_tf
    ON strategy_instances(exchange, market_type, symbol, price_type, timeframe);

CREATE INDEX IF NOT EXISTS idx_jobs_ready_queue
    ON jobs(status, scheduled_at, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_lease
    ON jobs(status, lease_expires_at, locked_by_worker);
CREATE INDEX IF NOT EXISTS idx_jobs_serialization_status
    ON jobs(serialization_key, status, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_strategy_status
    ON jobs(strategy_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_symbol_status_created
    ON orders(symbol, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_strategy_status_created
    ON orders(strategy_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_orders_account_status_created
    ON orders(account_id, status, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_fills_strategy_time
    ON fills(strategy_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_created
    ON backtest_runs(created_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_status_time
    ON backtest_runs(strategy_id, status, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_artifacts_type_created
    ON backtest_artifacts(artifact_type, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_candle_manifests_active_range
    ON candle_manifests(exchange, market_type, symbol, price_type, timeframe, is_active, min_open_time, max_open_time);
CREATE INDEX IF NOT EXISTS idx_candle_manifests_updated
    ON candle_manifests(updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_data_gaps_status_instrument
    ON data_gaps(status, exchange, market_type, symbol, price_type, timeframe, gap_start);

CREATE INDEX IF NOT EXISTS idx_events_type_time
    ON events(event_type, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_status_time
    ON events(status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_events_aggregate
    ON events(aggregate_type, aggregate_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_event_consumers_event_id
    ON event_consumers(event_id);
CREATE INDEX IF NOT EXISTS idx_event_consumers_status
    ON event_consumers(status, handled_at DESC);

CREATE INDEX IF NOT EXISTS idx_parquet_manifests_dataset_time
    ON parquet_manifests(dataset_type, exchange, market_type, symbol, price_type, timeframe, min_time, max_time);

CREATE INDEX IF NOT EXISTS idx_feature_requirements_status
    ON feature_requirements(status, updated_at DESC);
CREATE INDEX IF NOT EXISTS idx_feature_requirements_instrument
    ON feature_requirements(instrument_key, timeframe, from_time, to_time);

INSERT OR REPLACE INTO openpine_schema_metadata(key, value, updated_at)
VALUES
    ('schema_contract', 'openpine.sqlite.v4', strftime('%s', 'now')),
    ('schema_index_profile', 'openpine.sqlite.v4.indexes.011', strftime('%s', 'now'));
