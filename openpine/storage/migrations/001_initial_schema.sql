-- 001_initial_schema.sql
-- Core tables for OpenPine Phase 1

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY DEFAULT '',
    applied_at INTEGER NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    checksum TEXT NOT NULL DEFAULT '',
    id INTEGER NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE
);

CREATE TABLE IF NOT EXISTS pine_sources (
    id TEXT PRIMARY KEY,
    pine_id TEXT UNIQUE,
    name TEXT NOT NULL UNIQUE,
    source_path TEXT,
    source_hash TEXT,
    source_text TEXT NOT NULL,
    version TEXT DEFAULT '1.0.0',
    source_type TEXT NOT NULL DEFAULT 'unknown',
    active_artifact_id TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pine_artifacts (
    artifact_id TEXT PRIMARY KEY,
    pine_id TEXT NOT NULL,
    source_hash TEXT NOT NULL,
    pine2ast_version TEXT NOT NULL,
    ast2python_version TEXT NOT NULL,
    pinelib_contract_version TEXT NOT NULL,
    compile_options_hash TEXT NOT NULL,
    requirements_hash TEXT,
    ast_path TEXT NOT NULL,
    generated_py_path TEXT NOT NULL,
    compile_meta_path TEXT NOT NULL,
    requirements_path TEXT,
    ast_hash TEXT,
    generated_py_hash TEXT,
    compile_status TEXT NOT NULL,
    compile_log_path TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (pine_id) REFERENCES pine_sources(id),
    UNIQUE (pine_id, source_hash, pine2ast_version, ast2python_version, pinelib_contract_version, compile_options_hash)
);

CREATE TABLE IF NOT EXISTS compile_artifacts (
    id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    compile_meta TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (source_id) REFERENCES pine_sources(id)
);

CREATE TABLE IF NOT EXISTS strategy_instances (
    id TEXT PRIMARY KEY,
    strategy_id TEXT UNIQUE,
    name TEXT UNIQUE,
    pine_id TEXT,
    artifact_id TEXT NOT NULL,
    params_json TEXT NOT NULL DEFAULT '{}',
    params_hash TEXT NOT NULL,
    symbol TEXT NOT NULL,
    exchange TEXT DEFAULT 'BINANCE',
    market_type TEXT NOT NULL DEFAULT 'spot',
    price_type TEXT NOT NULL DEFAULT 'trade',
    timeframe TEXT NOT NULL,
    data_provider TEXT NOT NULL DEFAULT 'local',
    execution_provider TEXT NOT NULL DEFAULT 'paper',
    mode TEXT NOT NULL DEFAULT 'disabled',
    enabled INTEGER NOT NULL DEFAULT 0,
    live_enabled INTEGER NOT NULL DEFAULT 0,
    risk_profile_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    FOREIGN KEY (pine_id) REFERENCES pine_sources(id),
    FOREIGN KEY (artifact_id) REFERENCES compile_artifacts(id)
);

CREATE TABLE IF NOT EXISTS instruments (
    instrument_id TEXT PRIMARY KEY,
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    base TEXT,
    quote TEXT,
    contract_type TEXT NOT NULL,
    price_type TEXT NOT NULL,
    tick_size REAL,
    step_size REAL,
    min_qty REAL,
    min_notional REAL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (exchange, market_type, symbol, price_type)
);

CREATE TABLE IF NOT EXISTS strategy_state_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    params_hash TEXT NOT NULL,
    instrument_key TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    runtime_version TEXT NOT NULL,
    state_schema_version TEXT NOT NULL,
    last_processed_bar_time INTEGER NOT NULL,
    state_path TEXT NOT NULL,
    checksum TEXT NOT NULL,
    status TEXT NOT NULL,
    invalidation_reason TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_instances(id)
);

CREATE TABLE IF NOT EXISTS jobs (
    id TEXT PRIMARY KEY,
    job_id TEXT UNIQUE,
    job_type TEXT NOT NULL,
    strategy_id TEXT,
    status TEXT NOT NULL DEFAULT 'pending',
    idempotency_key TEXT,
    dedupe_key TEXT,
    serialization_key TEXT,
    priority INTEGER NOT NULL DEFAULT 20,
    created_at INTEGER NOT NULL,
    updated_at INTEGER,
    scheduled_at INTEGER,
    started_at INTEGER,
    finished_at INTEGER,
    progress_current INTEGER DEFAULT 0,
    progress_total INTEGER DEFAULT 0,
    error TEXT,
    error_message TEXT,
    input_json TEXT NOT NULL DEFAULT '{}',
    result_json TEXT,
    locked_by_worker TEXT,
    locked_at INTEGER,
    lease_expires_at INTEGER,
    worker_heartbeat_at INTEGER,
    retry_count INTEGER NOT NULL DEFAULT 0,
    max_retries INTEGER NOT NULL DEFAULT 3,
    UNIQUE (idempotency_key)
);

CREATE TABLE IF NOT EXISTS accounts (
    account_id TEXT PRIMARY KEY,
    id TEXT NOT NULL UNIQUE,
    name TEXT NOT NULL UNIQUE,
    provider TEXT NOT NULL DEFAULT 'unknown',
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL DEFAULT 'spot',
    mode TEXT NOT NULL DEFAULT 'paper',
    account_type TEXT NOT NULL DEFAULT 'paper',
    api_key_ref TEXT,
    api_secret_ref TEXT,
    api_key_hash TEXT,
    secret_hash TEXT,
    live_enabled INTEGER NOT NULL DEFAULT 0,
    permissions TEXT,
    config TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS data_requirements (
    requirement_id TEXT PRIMARY KEY,
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price_type TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    from_time INTEGER,
    to_time INTEGER,
    reason TEXT,
    required_by_strategy_ids TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (exchange, market_type, symbol, price_type, timeframe, provider, from_time, to_time)
);

CREATE TABLE IF NOT EXISTS aggregation_requirements (
    requirement_id TEXT PRIMARY KEY,
    instrument_key TEXT NOT NULL,
    source_timeframe TEXT NOT NULL,
    target_timeframe TEXT NOT NULL,
    from_time INTEGER,
    to_time INTEGER,
    required_by_strategy_ids TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (instrument_key, source_timeframe, target_timeframe, from_time, to_time)
);

CREATE TABLE IF NOT EXISTS feature_requirements (
    requirement_id TEXT PRIMARY KEY,
    instrument_key TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    expression_key TEXT NOT NULL,
    feature_key_hash TEXT NOT NULL,
    from_time INTEGER,
    to_time INTEGER,
    required_by_strategy_ids TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (feature_key_hash, from_time, to_time)
);

CREATE TABLE IF NOT EXISTS parquet_manifests (
    manifest_id TEXT PRIMARY KEY,
    dataset_type TEXT NOT NULL,
    exchange TEXT,
    market_type TEXT,
    symbol TEXT,
    price_type TEXT,
    timeframe TEXT,
    feature_key_hash TEXT,
    partition_path TEXT NOT NULL UNIQUE,
    min_time INTEGER,
    max_time INTEGER,
    row_count INTEGER NOT NULL,
    schema_hash TEXT NOT NULL,
    checksum TEXT NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    aggregate_type TEXT,
    aggregate_id TEXT,
    payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'NEW',
    created_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS event_consumers (
    consumer TEXT NOT NULL,
    event_id TEXT NOT NULL,
    status TEXT NOT NULL,
    handled_at INTEGER,
    error_message TEXT,
    PRIMARY KEY (consumer, event_id),
    FOREIGN KEY (event_id) REFERENCES events(event_id)
);

CREATE INDEX IF NOT EXISTS idx_pine_sources_name ON pine_sources(name);
CREATE INDEX IF NOT EXISTS idx_jobs_status_priority ON jobs(status, priority DESC, created_at);
CREATE INDEX IF NOT EXISTS idx_jobs_dedupe_active ON jobs(dedupe_key, status);
CREATE INDEX IF NOT EXISTS idx_accounts_provider_exchange ON accounts(provider, exchange, market_type);
CREATE INDEX IF NOT EXISTS idx_accounts_live_enabled ON accounts(live_enabled);
