-- Persist planned/active exit order prices on completed backtest trades.
-- These are sourced from the actual fill order, not inferred from exit_id labels.
ALTER TABLE backtest_trades ADD COLUMN stop_price REAL;
ALTER TABLE backtest_trades ADD COLUMN take_profit_price REAL;
