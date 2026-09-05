-- 021_strategy_execution_epochs.sql
-- Durable PAPER activation epochs. Replay is restart-safe within an epoch and
-- pause/resume starts a fresh paper account without evaluating paused bars.

CREATE TABLE IF NOT EXISTS strategy_execution_epochs (
    strategy_id TEXT PRIMARY KEY,
    mode TEXT NOT NULL,
    started_at INTEGER NOT NULL,
    FOREIGN KEY (strategy_id) REFERENCES strategy_instances(strategy_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_strategy_execution_epochs_mode_started
    ON strategy_execution_epochs(mode, started_at);

INSERT OR IGNORE INTO strategy_execution_epochs(strategy_id, mode, started_at)
SELECT strategy_id, 'paper', updated_at
FROM strategy_instances
WHERE strategy_id IS NOT NULL
  AND enabled = 1
  AND status = 'running'
  AND mode = 'paper';
