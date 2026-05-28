-- 005_plot_outputs_schema.sql
-- Add plot_outputs_path to backtest_runs for plot artifact persistence.

ALTER TABLE backtest_runs ADD COLUMN plot_outputs_path TEXT;
