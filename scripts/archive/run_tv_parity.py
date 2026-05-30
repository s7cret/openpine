"""Run OpenPine backtest on exact TradingView CSV candles.

This script bypasses the DataOrchestrator (which fetches from Binance API)
and uses CandleStorage.read_candles() directly to ensure the exact TV
OHLCV is used for the backtest.
"""
from __future__ import annotations

import sys
sys.path.insert(0, '[local-home]/[workspace-root]/workspace')
sys.path.insert(0, '[local-home]/pine2ast')
sys.path.insert(0, '[local-home]/ast2python')
sys.path.insert(0, '[local-home]/pinelib')
sys.path.insert(0, '[local-home]/backtest_engine')
sys.path.insert(0, '[local-home]/marketdata-provider')

import json
import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from openpine.config import OpenPineConfig
from openpine.data.candle_storage import CandleStorage
from openpine.data.bar_query import BarQuery
from openpine.data.contracts import WriteMode
from openpine.registry import SQLiteStrategyRegistry
from openpine.artifacts import ArtifactStore
from openpine.storage import BacktestResultStore, BacktestRunRequest
from openpine.runtime.engine import (
    BacktestEngineAdapter,
    BacktestRunConfig,
    load_strategy_class_from_artifact,
)

# ── CONFIG ──────────────────────────────────────────────────────────
TV_CSV = "[local-home]/[workspace-root]/workspace/pine_oracle_500_tv_exports_20260512_222247/exported/185_real_ema_cross_strategy_oracle/tv_185_real_ema_cross_strategy_oracle_chart.csv"
STRATEGY_ID = "strat_9d430a2a319f77b2_1779966579569"
REPORT_DIR = Path("[local-home]/[workspace-root]/repo-root/reports/op_tv_007")

# ── LOAD TV CSV ─────────────────────────────────────────────────────
print("=" * 60)
print("Phase B: Load and normalize TradingView CSV")
print("=" * 60)

df_tv = pd.read_csv(TV_CSV)
print(f"TV CSV shape: {df_tv.shape}")
print(f"TV columns: {list(df_tv.columns)}")
print(f"TV time range: {df_tv['time'].min()} - {df_tv['time'].max()}")
print(f"TV date range: {datetime.fromtimestamp(df_tv['time'].min())} - {datetime.fromtimestamp(df_tv['time'].max())}")

# Detect timestamp unit
if df_tv["time"].max() < 2_000_000_000:
    print("Detected: timestamps in SECONDS")
    df_tv["time_ms"] = df_tv["time"] * 1000
else:
    print("Detected: timestamps in MILLISECONDS")
    df_tv["time_ms"] = df_tv["time"]

# Timeframe detection
td = df_tv["time"].diff().dropna().mode().iloc[0]
print(f"Detected interval: {td} seconds = {td / 60} minutes")

# ── LOAD INTO CANDLE STORAGE ────────────────────────────────────────
print("\n" + "=" * 60)
print("Phase C: Load candles into OpenPine storage")
print("=" * 60)

from marketdata_provider.core.bar import Bar

bars = []
for _, row in df_tv.iterrows():
    bars.append(Bar(
        time=int(row["time_ms"]),
        open=float(row["open"]),
        high=float(row["high"]),
        low=float(row["low"]),
        close=float(row["close"]),
        volume=float(row["Volume"]),
        time_close=int(row["time_ms"] + int(td * 1000)),
    ))

storage = CandleStorage()
result = storage.write_candles(
    bars,
    mode=WriteMode.UPSERT_PARTITION,
    instrument_key="binance:spot:BTCUSDT:trade",
    timeframe="15m",
)
print(f"Written to storage: {result.rows_written} rows, {len(result.manifests_created)} manifest(s)")

# ── GET STRATEGY ────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Phase D: Load strategy")
print("=" * 60)

registry = SQLiteStrategyRegistry()
s = registry.get_strategy(STRATEGY_ID)
print(f"Strategy: {s.name}")
print(f"  symbol: {s.symbol}, timeframe: {s.timeframe}")
print(f"  artifact: {s.artifact_id}")
print(f"  params: {s.params_json}")
print(f"  exchange: {s.exchange}, market: {s.market_type}")

strategy_class = load_strategy_class_from_artifact(
    s.pine_id, s.artifact_id, symbol=s.symbol, timeframe=s.timeframe
)
print(f"  loaded class: {strategy_class.__name__}")

# ── RUN BACKTEST ────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Phase D: Run backtest on exact TV candles")
print("=" * 60)

start_ms = int(df_tv["time_ms"].min())
end_ms = int(df_tv["time_ms"].max())

# Use CandleStorage for canonical read
query = BarQuery(
    instrument_key="binance:spot:BTCUSDT:trade",
    timeframe="15m",
    from_time=start_ms,
    to_time=end_ms,
)
backtest_bars = storage.read_candles(query)
print(f"Canonical bars for backtest: {len(backtest_bars)}")
print(f"  first: {datetime.fromtimestamp(backtest_bars[0].time / 1000)}")
print(f"  last:  {datetime.fromtimestamp(backtest_bars[-1].time / 1000)}")

# Load declaration for config
artifact_store = ArtifactStore()
artifact = artifact_store.get_artifact(s.artifact_id, s.pine_id)
compile_meta = artifact.get("compile_meta", {})
declaration = compile_meta.get("translation_metadata", {}).get("declaration", {})
decl_args = declaration.get("arguments", {})

params = json.loads(s.params_json) if s.params_json else {}
config = BacktestRunConfig(
    symbol=s.symbol,
    timeframe=s.timeframe,
    start_time=start_ms,
    end_time=end_ms,
    initial_capital=decl_args.get("initial_capital", 10000.0),
    default_qty_type=decl_args.get("default_qty_type", "fixed"),
    default_qty_value=decl_args.get("default_qty_value", 1.0),
    commission_type=decl_args.get("commission_type", "none"),
    commission_value=decl_args.get("commission_value", 0.0),
    exit_matching=decl_args.get("close_entries_rule", "fifo").upper(),
    pyramiding=decl_args.get("pyramiding", 0),
)

_backend = None
_strategy_class = strategy_class
if hasattr(strategy_class, "generated_strategy_class_ref"):
    _strategy_class = strategy_class.generated_strategy_class_ref
    from backtest_engine.execution_backends.pine_runtime import PineRuntimeBackend
    _backend = PineRuntimeBackend()

result = BacktestEngineAdapter().run(
    _strategy_class,
    backtest_bars,
    config,
    params=params,
    execution_backend=_backend,
)

print(f"\nBacktest result:")
print(f"  status: {result.status}")
print(f"  bars: {result.bars_processed}")
print(f"  trades: {len(result.raw_result.trades)} closed + {len(result.raw_result.open_trades)} open")
print(f"  final equity: {result.raw_result.final_equity}")
print(f"  net profit: {result.raw_result.net_profit}")

# ── SAVE ARTIFACTS ──────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Phase E: Save artifacts")
print("=" * 60)

REPORT_DIR.mkdir(parents=True, exist_ok=True)
ts = int(datetime.now(timezone.utc).timestamp() * 1000)
run_id = f"run_tv_{ts}"

# Equity curve
eq_rows = []
for i, eq in enumerate(getattr(result.raw_result, "equity_curve", []) or []):
    eq_rows.append({
        "bar_index": i,
        "time": getattr(eq, "time", 0),
        "equity": getattr(eq, "equity", 0),
        "netprofit": getattr(eq, "netprofit", 0),
        "cash": getattr(eq, "cash", 0),
    })

if eq_rows:
    eq_df = pd.DataFrame(eq_rows)
    eq_path = REPORT_DIR / "equity_curve.parquet"
    pq.write_table(pa.Table.from_pandas(eq_df), str(eq_path))
    print(f"Equity curve: {eq_path} ({len(eq_df)} rows)")

# Trades
trades_rows = []
for t in result.raw_result.trades:
    trades_rows.append({
        "entry_time": t.entry_time,
        "exit_time": t.exit_time,
        "direction": t.direction,
        "qty": t.qty,
        "entry_price": t.entry_price,
        "exit_price": t.exit_price,
        "profit": t.profit,
        "net_pnl": getattr(t, "net_pnl", t.profit),
    })

if trades_rows:
    trades_df = pd.DataFrame(trades_rows)
    trades_path = REPORT_DIR / "trades.parquet"
    pq.write_table(pa.Table.from_pandas(trades_df), str(trades_path))
    print(f"Trades: {trades_path} ({len(trades_df)} rows)")

# Plot outputs
plots = getattr(result.raw_result, "plots", None)
if plots and isinstance(plots, list) and len(plots) > 0:
    # plots is list of tuples: (time, bar_index, value, title)
    import numpy as np
    plot_rows = []
    for p in plots:
        if len(p) >= 4:
            val = p[2]
            # Handle na strings and PineNASentinel
            if val == 'na' or type(val).__name__ == 'PineNASentinel':
                val = np.nan
            else:
                try:
                    val = float(val)
                except (ValueError, TypeError):
                    val = np.nan
            plot_rows.append({
                "time": int(p[0]),
                "bar_index": int(p[1]),
                "value": val,
                "title": str(p[3]),
            })
    if plot_rows:
        plot_df = pd.DataFrame(plot_rows)
        plot_path = REPORT_DIR / "plot_outputs.parquet"
        pq.write_table(pa.Table.from_pandas(plot_df), str(plot_path))
        print(f"Plot outputs: {plot_path} ({len(plot_df)} rows)")
        print(f"  unique titles: {sorted(plot_df['title'].dropna().unique())}")

# Save TV CSV used
df_tv.to_parquet(REPORT_DIR / "tv_ohlcv_used.parquet")
print(f"TV OHLCV: {REPORT_DIR / 'tv_ohlcv_used.parquet'}")

# ── SUMMARY ─────────────────────────────────────────────────────────
print("\n" + "=" * 60)
print("Summary")
print("=" * 60)
print(f"Run ID: {run_id}")
print(f"Strategy: {s.name}")
print(f"Bars: {len(backtest_bars)}")
print(f"Trades: {len(trades_rows)}")
print(f"Final equity: {result.raw_result.final_equity}")
print(f"Artifacts: {REPORT_DIR}")
print("=" * 60)
