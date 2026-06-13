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
    net_profit_pct REAL,
    gross_profit REAL,
    gross_loss REAL,
    profit_factor REAL,
    max_drawdown REAL,
    max_drawdown_pct REAL,
    sharpe REAL,
    sortino REAL,
    calmar REAL,
    win_rate REAL,
    trades_total INTEGER DEFAULT 0,
    winning_trades INTEGER DEFAULT 0,
    losing_trades INTEGER DEFAULT 0,

    avg_trade REAL,
    avg_win REAL,
    avg_loss REAL,
    largest_win REAL,
    largest_loss REAL,
    avg_bars_in_trade REAL,
    commission_total REAL,
    expectancy REAL,

    result_json TEXT,
    report_path TEXT,
    equity_curve_path TEXT,
    bar_outputs_path TEXT,
    error_message TEXT,
    traceback_id TEXT,

    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_strategy_time
    ON backtest_runs(strategy_id, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_symbol_tf
    ON backtest_runs(symbol, timeframe, started_at DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_runs_status
    ON backtest_runs(status, started_at DESC);

CREATE TABLE IF NOT EXISTS backtest_trades (
    trade_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,

    entry_id TEXT,
    exit_id TEXT,
    direction TEXT NOT NULL,

    entry_time INTEGER NOT NULL,
    exit_time INTEGER,
    entry_price REAL NOT NULL,
    exit_price REAL,

    qty REAL NOT NULL,
    gross_pnl REAL,
    net_pnl REAL,
    net_pnl_pct REAL,
    fee REAL,
    slippage REAL,

    bars_held INTEGER,
    exit_reason TEXT,

    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_trades_run
    ON backtest_trades(run_id, entry_time);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_strategy
    ON backtest_trades(strategy_id, entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_backtest_trades_pnl
    ON backtest_trades(run_id, net_pnl);

CREATE TABLE IF NOT EXISTS backtest_artifacts (
    artifact_row_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,

    artifact_type TEXT NOT NULL,
    path TEXT NOT NULL,
    format TEXT NOT NULL,
    row_count INTEGER,
    min_time INTEGER,
    max_time INTEGER,
    schema_hash TEXT,

    created_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_backtest_artifacts_run
    ON backtest_artifacts(run_id, artifact_type);
CREATE INDEX IF NOT EXISTS idx_backtest_artifacts_strategy
    ON backtest_artifacts(strategy_id, created_at DESC);
