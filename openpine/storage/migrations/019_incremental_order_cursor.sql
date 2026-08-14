-- 019_incremental_order_cursor.sql
-- Support bounded updated_after order polling without scanning a strategy ledger.

CREATE INDEX IF NOT EXISTS idx_orders_strategy_updated
    ON orders(strategy_id, updated_at, order_id);
CREATE INDEX IF NOT EXISTS idx_orders_updated
    ON orders(updated_at, order_id);

INSERT OR REPLACE INTO openpine_schema_metadata(key, value, updated_at)
VALUES
    ('schema_index_profile', 'openpine.sqlite.v4.indexes.019', strftime('%s', 'now'));
