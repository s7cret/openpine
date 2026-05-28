# OP-TV-007: TradingView Parity Audit Report

**Date:** 2026-05-28
**Strategy:** P092_Real_EMA_Cross_Strategy_Oracle (BTCUSDT 15m)
**TV Export:** `185_real_ema_cross_strategy_oracle`
**OpenPine Run:** `run_tv_1779967473359`

---

## Executive Summary

| Metric | TradingView | OpenPine | Delta | Status |
|--------|-------------|----------|-------|--------|
| Bars processed | 300 | 300 | 0 | ✅ MATCH |
| OHLCV | 300 rows | 300 rows | Identical | ✅ MATCH |
| Closed trades | Unknown (pre-existing state) | 16 | N/A | ⚠️ DIFF |
| Long signals | 8 | 9 | +1 | ⚠️ DIFF |
| Short signals | 7 | 8 | +1 | ⚠️ DIFF |
| Final equity | ~9301 (NaN last bar) | 9977.18 | -676 | ❌ DIFF |
| EMA9 MAE | — | — | 13.05 max | ⚠️ DIFF |
| EMA21 MAE | — | — | 215.95 max | ❌ DIFF |
| RSI14 MAE | — | — | 3.09 max | ✅ CLOSE |

**Root Cause:** TV export is a **window from bar 12500+ of a longer backtest**. OpenPine ran fresh on 300 bars without 12500 bars of pre-history. EMA warmup + pre-existing trades caused divergence.

---

## Phase A: TV Export Inventory

- **Files found:** 19 strategy CSV exports
- **Selected:** `185_real_ema_cross_strategy_oracle`
- **Symbol/Timeframe:** BTCUSDT, 15m
- **Rows:** 300 bars × 61 columns
- **Time range:** 2026-05-11 08:00:00 → 2026-05-14 10:45:00 (3 days, 2h45m)
- **Timestamp unit:** Seconds (Unix epoch)
- **Interval:** 900s = 15m ✅

## Phase B: Strategy Registration

- **Pine Source:** `P092_ema_cross` (092 Real EMA Cross Strategy Oracle)
- **Compilation:** OK (`art_ab88da847718c8f2`)
- **Strategy Config:** BTCUSDT 15m, backtest mode
- **Parameters:** `commission_value=0.055`, `default_qty_type=percent_of_equity`, `default_qty_value=10.0`
- **Note:** Removed `max_bars_back=5000` from source (compile-time limitation)

## Phase C: Data Ingestion

- **Method:** Exact TV CSV candles loaded into CandleStorage via `WriteMode.UPSERT_PARTITION`
- **Instrument key:** `binance:spot:BTCUSDT:trade`
- **Canonical read:** 300 bars (no duplicates after OP-DATA-006 dedup fix)

## Phase D: Backtest Execution

```
Status: completed
Bars: 300
Trades: 16 closed + 0 open
Final equity: 9977.17827527755
Net profit: -22.82172472244929
```

## Phase E: Parity Comparison

### E.1 OHLCV — PERFECT MATCH

All 300 bars identical between TV CSV and OpenPine canonical read.

### E.2 Signals — DIVERGENT

| Signal Type | TV Count | OP Count | Delta |
|-------------|----------|----------|-------|
| LONG | 8 | 9 | +1 |
| SHORT | 7 | 8 | +1 |

**Extra OP signals:**
- Long at 1778476500 (bar 1) — TV has no signal here
- Short at 1778482800 (bar 8) — TV has no signal here

**Reason:** TV EMAs computed over 12500+ bars; OP EMAs warmed up over 300 bars. Crossover points differ.

### E.3 Equity Curve — MAJOR DIVERGENCE

| Statistic | Value |
|-----------|-------|
| Max diff | -674.22 |
| Min diff | -681.87 |
| Mean diff | -677.55 |

TV equity starts at ~9319 (already down 681 from initial 10000), indicating **pre-existing trades** from before the visible window. OP starts at 10000.

### E.4 Indicator Plots

| Indicator | Max Abs Diff | Mean Diff | Assessment |
|-----------|-------------|-----------|------------|
| EMA9 | 13.05 | 0.22 | ⚠️ Warmup drift |
| EMA21 | 215.95 | 7.94 | ❌ Large drift (longer period = more history sensitive) |
| RSI14 | 3.09 | -0.17 | ✅ Close (bounded oscillator) |

### E.5 Trade Analysis

**OP Trades (16):**
- 8 longs, 8 shorts
- Qty ~0.0124 BTC per trade (10% equity sizing)
- Average loss per trade: ~-2.5 to -7.8
- Biggest winner: +12.92 (short from 80854→79719)

**TV Trades:** Unknown exact count. TV `P092_CLOSED_TRADES` column is NaN in export.

---

## Phase F: Root Cause Classification

| # | Discrepancy | Root Cause | Fixable? | Priority |
|---|-------------|------------|----------|----------|
| 1 | EMA9/EMA21 values differ | TV has 12500 bars pre-history; OP starts fresh | Partially (need full history) | P1 |
| 2 | Extra signals (1 long, 1 short) | Same as #1 — crossover timing shifts without history | Same as #1 | P1 |
| 3 | Equity offset (~677) | TV has open trades from pre-history | No (need full state snapshot) | P2 |
| 4 | Trade count mismatch | Same as #1 + #3 | Same | P2 |
| 5 | RSI14 close match | Bounded oscillator converges fast | N/A | — |

---

## Phase G: Recommendations

### Immediate (for parity testing)
1. **Use full history:** Re-export TV with `max_bars_back=0` or run from the very first bar
2. **Use `startTime` filter:** Ensure both TV and OP use the same `startTime` input to align visible windows
3. **Use no-history indicators:** For pure parity, test with SMA instead of EMA, or use a strategy with fixed threshold (e.g., RSI level) rather than trend-following

### Code Fixes Needed
1. **Fix `BacktestEngineAdapter._to_engine_bar()`** to accept both `bar.time` and `bar.timestamp` ✅ DONE
2. **Fix plot serialization** for PineNASentinel values ✅ DONE

---

## Artifacts

| File | Description |
|------|-------------|
| `reports/op_tv_007/tv_ohlcv_used.parquet` | Original TV CSV (300×61) |
| `reports/op_tv_007/equity_curve.parquet` | OP equity curve (300 rows) |
| `reports/op_tv_007/trades.parquet` | OP trades (16 rows) |
| `reports/op_tv_007/plot_outputs.parquet` | OP plot outputs (16,500 rows, 55 series) |

---

## Conclusion

**OHLCV is 100% identical.** Indicator divergence is explained by TV's 12500-bar pre-history vs OpenPine's 300-bar fresh start. To achieve true parity, OpenPine needs access to the full bar history preceding the visible window, or the strategy must use history-independent logic.

**Status:** OP-TV-007 Phase A-E complete. Phase F-G documented.
