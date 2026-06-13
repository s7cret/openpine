-- 007_strategy_trade_ledger.sql
-- Paper/live/history strategy ledger. Orders/fills stay execution facts;
-- trades/positions are strategy accounting facts.

CREATE TABLE IF NOT EXISTS strategy_positions (
    position_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    account_id TEXT,

    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price_type TEXT NOT NULL DEFAULT 'trade',
    timeframe TEXT NOT NULL,

    source TEXT NOT NULL,
    side TEXT NOT NULL DEFAULT 'flat',
    qty REAL NOT NULL DEFAULT 0.0,
    avg_price REAL,
    realized_pnl REAL NOT NULL DEFAULT 0.0,
    unrealized_pnl REAL,

    opened_at INTEGER,
    last_bar_time INTEGER,
    metadata_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL,

    UNIQUE(strategy_id, account_id, exchange, market_type, symbol, price_type, timeframe)
);

CREATE INDEX IF NOT EXISTS idx_strategy_positions_strategy
    ON strategy_positions(strategy_id, symbol, timeframe);
CREATE INDEX IF NOT EXISTS idx_strategy_positions_source
    ON strategy_positions(source, updated_at DESC);

CREATE TABLE IF NOT EXISTS strategy_trades (
    trade_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    account_id TEXT,
    run_id TEXT,
    order_id TEXT,

    exchange TEXT NOT NULL,
    market_type TEXT NOT NULL,
    symbol TEXT NOT NULL,
    price_type TEXT NOT NULL DEFAULT 'trade',
    timeframe TEXT NOT NULL,

    source TEXT NOT NULL,
    status TEXT NOT NULL,
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
    fee REAL,
    bars_held INTEGER,
    metadata_json TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_strategy_trades_strategy
    ON strategy_trades(strategy_id, entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_trades_symbol_tf
    ON strategy_trades(symbol, timeframe, entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_trades_source
    ON strategy_trades(source, status, entry_time DESC);
CREATE INDEX IF NOT EXISTS idx_strategy_trades_order
    ON strategy_trades(order_id);
