-- 002_orders_schema.sql
-- Orders table for OpenPine — sections 30.7, 33.2

CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY,
    strategy_id TEXT NOT NULL,
    account_id TEXT,
    provider_order_id TEXT,
    client_order_id TEXT NOT NULL UNIQUE,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    order_type TEXT NOT NULL,
    qty REAL NOT NULL,
    limit_price REAL,
    stop_price REAL,
    status TEXT NOT NULL,
    reduce_only INTEGER NOT NULL DEFAULT 0,
    filled_quantity REAL NOT NULL DEFAULT 0,
    avg_fill_price REAL,
    intent_json TEXT NOT NULL,
    risk_decision_json TEXT,
    error TEXT,
    created_at INTEGER NOT NULL,
    updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS fills (
    fill_id TEXT PRIMARY KEY,
    order_id TEXT NOT NULL,
    strategy_id TEXT NOT NULL,
    provider_fill_id TEXT,
    symbol TEXT NOT NULL,
    side TEXT NOT NULL,
    qty REAL NOT NULL,
    price REAL NOT NULL,
    fee REAL,
    fee_asset TEXT,
    fill_time INTEGER NOT NULL,
    created_at INTEGER NOT NULL,
    FOREIGN KEY (order_id) REFERENCES orders(order_id)
);

CREATE INDEX IF NOT EXISTS idx_orders_account_id ON orders(account_id);
CREATE INDEX IF NOT EXISTS idx_orders_strategy_id ON orders(strategy_id);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status);
CREATE INDEX IF NOT EXISTS idx_orders_client_order_id ON orders(client_order_id);
CREATE INDEX IF NOT EXISTS idx_fills_order_id ON fills(order_id);
CREATE INDEX IF NOT EXISTS idx_fills_strategy_id ON fills(strategy_id);
