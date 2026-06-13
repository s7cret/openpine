-- 004_data_lake_schema.sql
-- OP-DL-004: Candle Data Lake schema for OpenPine
-- Parquet-backed OHLCV storage with SQLite manifest tracking

-- candle_manifests: indexes OHLCV parquet partitions
-- partition_path is the unique key — one row per parquet file
CREATE TABLE IF NOT EXISTS candle_manifests (
    manifest_id TEXT PRIMARY KEY,
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price_type TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    partition_path TEXT NOT NULL UNIQUE,
    min_open_time INTEGER NOT NULL,
    max_open_time INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    schema_hash TEXT NOT NULL,
    checksum TEXT NOT NULL,
    file_size_bytes INTEGER,
    provider TEXT NOT NULL DEFAULT 'binance',
    ingested_at INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

-- data_requirements: what data needs to be loaded
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

-- aggregation_requirements: what aggregations are needed
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

-- data_gaps: gaps in the data
CREATE TABLE IF NOT EXISTS data_gaps (
    gap_id TEXT PRIMARY KEY,
    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price_type TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    provider TEXT NOT NULL,
    gap_start INTEGER NOT NULL,
    gap_end INTEGER NOT NULL,
    severity TEXT NOT NULL DEFAULT 'minor',
    status TEXT NOT NULL DEFAULT 'open',
    filled_by_job_id TEXT,
    filled_at INTEGER,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,
    UNIQUE (exchange, market_type, symbol, price_type, timeframe, provider, gap_start, gap_end)
);

-- Indexes for candle_manifests
CREATE INDEX IF NOT EXISTS idx_candle_manifests_instrument
    ON candle_manifests(exchange, market_type, symbol, price_type, timeframe);
CREATE INDEX IF NOT EXISTS idx_candle_manifests_time_range
    ON candle_manifests(exchange, market_type, symbol, price_type, timeframe, min_open_time, max_open_time);
CREATE INDEX IF NOT EXISTS idx_candle_manifests_provider
    ON candle_manifests(provider);

-- Indexes for data_requirements
CREATE INDEX IF NOT EXISTS idx_data_requirements_status
    ON data_requirements(status);
CREATE INDEX IF NOT EXISTS idx_data_requirements_instrument
    ON data_requirements(exchange, market_type, symbol, price_type, timeframe);

-- Indexes for aggregation_requirements
CREATE INDEX IF NOT EXISTS idx_aggregation_requirements_status
    ON aggregation_requirements(status);
CREATE INDEX IF NOT EXISTS idx_aggregation_requirements_instrument
    ON aggregation_requirements(instrument_key, source_timeframe, target_timeframe);

-- Indexes for data_gaps
CREATE INDEX IF NOT EXISTS idx_data_gaps_status
    ON data_gaps(status);
CREATE INDEX IF NOT EXISTS idx_data_gaps_instrument
    ON data_gaps(exchange, market_type, symbol, price_type, timeframe, provider);
