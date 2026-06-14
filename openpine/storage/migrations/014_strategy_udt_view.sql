-- 014_strategy_udt_view.sql
-- Derived view that flags UDT (User-Defined Type) usage per strategy.
-- Pine Script v6 UDT syntax: `type <Name>` at line start.
-- Detection is regex-free so SQLite doesn't need a user function.

CREATE VIEW IF NOT EXISTS v_strategy_udt AS
SELECT
    s.id AS strategy_instance_id,
    s.strategy_id AS strategy_key,
    s.pine_id,
    CASE
        WHEN p.source_text IS NULL THEN 0
        WHEN p.source_text LIKE 'type %'
            OR p.source_text LIKE ('%' || x'0a' || 'type %')
            OR p.source_text LIKE ('%' || x'0d' || x'0a' || 'type %')
        THEN 1
        ELSE 0
    END AS uses_udt
FROM strategy_instances s
LEFT JOIN pine_sources p ON p.id = s.pine_id;

-- Derived view: distinct timeframes per strategy (multi-TF detection)
CREATE VIEW IF NOT EXISTS v_strategy_timeframes AS
SELECT strategy_instance_id, COUNT(DISTINCT timeframe) AS tf_count
FROM strategy_instances
WHERE timeframe IS NOT NULL AND timeframe <> ''
GROUP BY strategy_instance_id;

-- Derived view: directional breadth per strategy (long + short = "both_sides")
CREATE VIEW IF NOT EXISTS v_strategy_directions AS
SELECT
    o.strategy_id AS strategy_instance_id,
    SUM(CASE WHEN o.direction IN ('long', 'buy', 'LONG', 'BUY') THEN 1 ELSE 0 END) AS long_count,
    SUM(CASE WHEN o.direction IN ('short', 'sell', 'SHORT', 'SELL') THEN 1 ELSE 0 END) AS short_count
FROM backtest_trades o
WHERE o.direction IS NOT NULL
GROUP BY o.strategy_id;
