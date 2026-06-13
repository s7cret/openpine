ALTER TABLE backtest_runs ADD COLUMN data_fingerprint TEXT;

CREATE INDEX IF NOT EXISTS idx_backtest_runs_data_fingerprint
ON backtest_runs(data_fingerprint);
