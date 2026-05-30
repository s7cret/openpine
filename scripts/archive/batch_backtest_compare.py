# DEPRECATED: archived 2026-05-30 — see scripts/archive/README.md
#!/usr/bin/env python3
"""Batch backtest all TV exports and compare with TV data.

For each exported strategy:
1. Load the TV chart CSV
2. Run backtest through pinelib
3. Save results (trades, equity, signals)
4. Compare with TV export data
"""
from __future__ import annotations

import sys
sys.path.insert(0, '[local-home]/pinelib')
sys.path.insert(0, '[local-home]/pine2ast')
sys.path.insert(0, '[local-home]/ast2python')
sys.path.insert(0, '[local-home]/backtest_engine')
sys.path.insert(0, '[local-home]/marketdata-provider')

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd

from pinelib import Bar, run_generated_strategy
from pinelib.io import load_bars


# ── CONFIG ──────────────────────────────────────────────────────────
TV_EXPORT_DIR = Path("[local-home]/[workspace-root]/workspace/pine_oracle_500_tv_exports_20260512_222247/exported")
OUTPUT_DIR = Path("[local-home]/[workspace-root]/repo-root/reports/op_tv_batch_parity")
REPORT_DATE = datetime.now().strftime("%Y%m%d_%H%M%S")

# Limit for quick test (set None for all)
LIMIT_STRATEGIES: Optional[int] = 10  # None = all


def detect_tv_type(folder: Path) -> str:
    """Detect if folder contains strategy or indicator."""
    pine_file = list(folder.glob("*.pine"))
    if not pine_file:
        return "unknown"
    
    content = pine_file[0].read_text().lower()
    if "strategy(" in content:
        return "strategy"
    elif "indicator(" in content:
        return "indicator"
    else:
        return "unknown"


def load_tv_chart(csv_path: Path) -> pd.DataFrame:
    """Load and normalize TV chart CSV."""
    df = pd.read_csv(csv_path)
    
    # Detect timestamp unit
    if df["time"].max() < 2_000_000_000:
        df["time_ms"] = df["time"] * 1000
    else:
        df["time_ms"] = df["time"]
    
    # Create datetime index
    if df["time_ms"].max() < 2_000_000_000:
        df["datetime"] = pd.to_datetime(df["time"], unit="s")
    else:
        df["datetime"] = pd.to_datetime(df["time"], unit="ms")
    
    return df


def load_tv_exports(folder: Path) -> dict:
    """Load TV export data (trades, equity, etc)."""
    exports = {}
    
    # Look for trade exports
    trade_csvs = list(folder.glob("*trades*.csv")) + list(folder.glob("*trade*.csv"))
    for csv_path in trade_csvs:
        name = csv_path.stem
        exports[name] = pd.read_csv(csv_path)
    
    # Look for equity exports
    equity_csvs = list(folder.glob("*equity*.csv")) + list(folder.glob("*equity*.json"))
    for csv_path in equity_csvs:
        name = csv_path.stem
        exports[name] = pd.read_csv(csv_path)
    
    return exports


def run_backtest(pine_code: str, bars_df: pd.DataFrame) -> dict:
    """Run backtest and extract key metrics."""
    try:
        # Convert to pinelib bars
        bars = []
        for _, row in bars_df.iterrows():
            bars.append(Bar(
                time=int(row["time_ms"]),
                open=float(row["open"]),
                high=float(row["high"]),
                low=float(row["low"]),
                close=float(row["close"]),
                volume=float(row.get("volume", row.get("Volume", 0))),
                time_close=int(row["time_ms"] + 900_000),  # 15m
            ))
        
        # Run backtest
        strategy = run_generated_strategy(pine_code, bars, params={})
        
        # Extract metrics
        metrics = {
            "closed_trades": strategy.closedtrades,
            "open_trades": strategy.opentrades,
            "equity": strategy.equity,
            "net_profit": strategy.net_profit,
            "open_profit": strategy.open_profit,
            "initial_capital": strategy.initial_capital,
        }
        
        # Get trade details if available
        if strategy.closedtrades > 0:
            metrics["trades"] = []
            for i in range(min(strategy.closedtrades, 100)):  # Cap at 100
                trade = {
                    "index": i,
                    "entry_price": strategy.closedtrades_entry_price(i),
                    "exit_price": strategy.closedtrades_exit_price(i),
                    "profit": strategy.closedtrades_profit(i),
                    "net_profit": strategy.closedtrades_net_profit(i),
                    "commission": strategy.closedtrades_commission(i),
                    "qty": strategy.closedtrades_qty(i),
                    "size": strategy.closedtrades_size(i),
                    "side": strategy.closedtrades_side(i),
                }
                metrics["trades"].append(trade)
        
        return {"status": "success", "metrics": metrics}
    
    except Exception as e:
        return {"status": "error", "error": str(e)}


def compare_results(op_metrics: dict, tv_exports: dict) -> dict:
    """Compare OpenPine results with TV exports."""
    comparison = {
        "closed_trades_match": None,
        "equity_diff": None,
        "trade_diff": [],
    }
    
    if "trades" in op_metrics and tv_exports:
        op_trades = op_metrics.get("trades", [])
        # Find matching TV trades CSV
        tv_trades = None
        for name, df in tv_exports.items():
            if "trade" in name.lower() and "exit" in str(df.get("Type", "")).lower():
                tv_trades = df
                break
        
        if tv_trades is not None:
            comparison["closed_trades_match"] = len(op_trades) == len(tv_trades)
            comparison["trade_count_op"] = len(op_trades)
            comparison["trade_count_tv"] = len(tv_trades)
    
    return comparison


def main():
    print("=" * 70)
    print(f"TV Export Batch Backtest & Compare")
    print(f"Output: {OUTPUT_DIR}")
    print("=" * 70)
    
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    
    # Get all exported strategy folders
    folders = sorted([f for f in TV_EXPORT_DIR.iterdir() if f.is_dir()])
    
    if LIMIT_STRATEGIES:
        folders = folders[:LIMIT_STRATEGIES]
        print(f"\nLIMIT: Running first {LIMIT_STRATEGIES} strategies (remove LIMIT_STRATEGIES for all)\n")
    
    print(f"Found {len(folders)} exported strategies")
    
    results = []
    
    for i, folder in enumerate(folders, 1):
        name = folder.name
        print(f"\n[{i}/{len(folders)}] Processing: {name}")
        
        result = {
            "folder": name,
            "type": detect_tv_type(folder),
            "status": "pending",
        }
        
        # Find TV chart
        chart_csvs = list(folder.glob("tv_*.csv"))
        if not chart_csvs:
            print(f"  ⚠️ No TV chart CSV found")
            result["status"] = "no_chart"
            results.append(result)
            continue
        
        chart_csv = chart_csvs[0]
        
        # Find Pine file
        pine_files = list(folder.glob("*.pine"))
        if not pine_files:
            print(f"  ⚠️ No Pine file found")
            result["status"] = "no_pine"
            results.append(result)
            continue
        
        pine_file = pine_files[0]
        
        try:
            # Load TV data
            tv_chart = load_tv_chart(chart_csv)
            tv_exports = load_tv_exports(folder)
            
            print(f"  TV chart: {len(tv_chart)} bars, {tv_chart['datetime'].min()} to {tv_chart['datetime'].max()}")
            print(f"  TV exports: {list(tv_exports.keys())}")
            
            # Run backtest
            pine_code = pine_file.read_text()
            backtest_result = run_backtest(pine_code, tv_chart)
            
            if backtest_result["status"] == "success":
                print(f"  ✅ Backtest: {backtest_result['metrics']['closed_trades']} closed, {backtest_result['metrics']['open_trades']} open")
                
                result["status"] = "success"
                result["metrics"] = backtest_result["metrics"]
                result["comparison"] = compare_results(backtest_result["metrics"], tv_exports)
                
                # Save results
                result_folder = OUTPUT_DIR / name
                result_folder.mkdir(exist_ok=True)
                
                # Save metrics as JSON
                with open(result_folder / "metrics.json", "w") as f:
                    json.dump(result, f, indent=2, default=str)
                
                # Save trades as CSV if available
                if "trades" in backtest_result["metrics"]:
                    trades_df = pd.DataFrame(backtest_result["metrics"]["trades"])
                    trades_df.to_csv(result_folder / "op_trades.csv", index=False)
                
            else:
                print(f"  ❌ Backtest error: {backtest_result.get('error', 'unknown')}")
                result["status"] = "error"
                result["error"] = backtest_result.get("error")
        
        except Exception as e:
            print(f"  ❌ Error: {e}")
            result["status"] = "error"
            result["error"] = str(e)
        
        results.append(result)
    
    # Save summary
    summary = {
        "date": datetime.now().isoformat(),
        "total": len(results),
        "success": sum(1 for r in results if r["status"] == "success"),
        "error": sum(1 for r in results if r["status"] == "error"),
        "no_chart": sum(1 for r in results if r["status"] == "no_chart"),
        "no_pine": sum(1 for r in results if r["status"] == "no_pine"),
        "results": results,
    }
    
    summary_path = OUTPUT_DIR / f"summary_{REPORT_DATE}.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total: {summary['total']}")
    print(f"Success: {summary['success']}")
    print(f"Error: {summary['error']}")
    print(f"No chart: {summary['no_chart']}")
    print(f"No Pine: {summary['no_pine']}")
    print(f"\nSaved to: {summary_path}")


if __name__ == "__main__":
    main()
