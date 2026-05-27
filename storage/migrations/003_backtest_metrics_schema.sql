-- 003_backtest_metrics_schema.sql
-- Backtest results persistence for OpenPine
-- Stores summary metrics in SQLite, large time-series in Parquet artifacts.

CREATE TABLE IF NOT EXISTS backtest_runs (
    run_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    pine_id TEXT NOT NULL,
    artifact_id TEXT NOT NULL,
    params_hash TEXT NOT NULL,

    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price_type TEXT NOT NULL DEFAULT 'trade',
    timeframe TEXT NOT NULL,

    from_time INTEGER,
    to_time INTEGER,
    warmup_bars INTEGER DEFAULT 0,

    status TEXT NOT NULL DEFAULT 'running',
    started_at INTEGER NOT NULL,
    finished_at INTEGER,

    initial_capital REAL,
    final_equity REAL,
    net_profit REAL,
    net_profit_percent REAL,
    gross_profit REAL,
    gross_loss REAL,
    profit_factor REAL,
    max_drawdown REAL,
    max_drawdown_percent REAL,
    sharpe_ratio REAL,
    sortino_ratio REAL,
    win_rate REAL,
    total_trades INTEGER,
    winning_trades INTEGER,
    losing_trades INTEGER,
    avg_trade REAL,
    avg_win REAL,
    avg_loss REAL,
    largest_win REAL,
    largest_loss REAL,
    avg_bars_in_trade REAL,
    commission_total REAL,
    expectancy REAL,

    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_id
    ON backtest_runs(strategy_id);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_status
    ON backtest_runs(status);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_started_at
    ON backtest_runs(started_at DESC);

CREATE TABLE IF NOT EXISTS backtest_trades (
    trade_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time INTEGER NOT NULL,
    entry_price REAL NOT NULL,
    exit_time INTEGER,
    exit_price REAL,
    qty REAL NOT NULL,
    profit REAL,
    profit_percent REAL,
    mfe REAL,
    mae REAL,
    exit_reason TEXT,
    bars_held INTEGER,
    is_open INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run_id
    ON backtest_trades(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_entry_time
    ON backtest_trades(entry_time);

CREATE TABLE IF NOT EXISTS backtest_artifacts (
    artifact_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    artifact_type TEXT NOT NULL,
    file_path TEXT NOT NULL,
    file_size INTEGER,
    row_count INTEGER,
    checksum TEXT,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (run_id) REFERENCES backtest_runs(run_id)
);

CREATE INDEX IF NOT EXISTS idx_backtest_artifacts_run_id
    ON backtest_artifacts(run_id);
CREATE INDEX IF NOT EXISTS idx_backtest_artifacts_type
    ON backtest_artifacts(artifact_type);
