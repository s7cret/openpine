# DEPRECATED: archived 2026-05-30 — see scripts/archive/README.md
#!/usr/bin/env python3
"""Batch compare TV exports - extract metrics from TV data and compare across all exports.

This script:
1. Scans all TV exported folders
2. Extracts metrics from TV export data (trades, equity)
3. Saves a summary comparing all strategies
"""
from __future__ import annotations

import sys
sys.path.insert(0, '[local-home]/pinelib')

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd


# ── CONFIG ──────────────────────────────────────────────────────────
TV_EXPORT_DIR = Path("[local-home]/[workspace-root]/workspace/pine_oracle_500_tv_exports_20260512_222247/exported")
OUTPUT_DIR = Path("[local-home]/[workspace-root]/repo-root/reports/op_tv_batch_parity")
REPORT_DATE = datetime.now().strftime("%Y%m%d_%H%M%S")

# Limit for quick test (set None for all)
LIMIT_STRATEGIES: Optional[int] = None  # None = all


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


def extract_tv_metrics(folder: Path) -> dict:
    """Extract metrics from TV export data."""
    metrics = {
        "type": detect_tv_type(folder),
        "has_chart": False,
        "has_trades": False,
        "has_equity": False,
        "has_signals": False,
        "trade_count": 0,
        "equity_start": None,
        "equity_end": None,
        "equity_max": None,
        "equity_min": None,
        "commission": None,
        "errors": [],
    }
    
    # Check for chart
    chart_csvs = list(folder.glob("tv_*.csv"))
    if chart_csvs:
        metrics["has_chart"] = True
        try:
            df = pd.read_csv(chart_csvs[0])
            # Detect timestamp
            if df["time"].max() > 2_000_000_000_000:
                # milliseconds
                metrics["chart_bars"] = len(df)
                metrics["chart_start"] = datetime.fromtimestamp(df["time"].min() / 1000).isoformat()
                metrics["chart_end"] = datetime.fromtimestamp(df["time"].max() / 1000).isoformat()
            elif df["time"].max() > 2_000_000_000:
                # seconds
                metrics["chart_bars"] = len(df)
                metrics["chart_start"] = datetime.fromtimestamp(df["time"].min()).isoformat()
                metrics["chart_end"] = datetime.fromtimestamp(df["time"].max()).isoformat()
        except Exception as e:
            metrics["errors"].append(f"chart: {e}")
    
    # Look for trades
    trade_csvs = list(folder.glob("*trades*.csv")) + list(folder.glob("*trade*.csv"))
    for csv_path in trade_csvs:
        try:
            df = pd.read_csv(csv_path)
            if "Trade #" in df.columns or "Type" in df.columns:
                metrics["has_trades"] = True
                metrics["trade_count"] = len(df)
                
                # Get commission if available
                if "Commission" in df.columns:
                    metrics["commission"] = df["Commission"].iloc[0] if not pd.isna(df["Commission"].iloc[0]) else None
                
                # Extract equity from trades
                if "Cum. Profit USD" in df.columns:
                    profits = df["Cum. Profit USD"].dropna()
                    if len(profits) > 0:
                        metrics["equity_start"] = float(profits.iloc[0])
                        metrics["equity_end"] = float(profits.iloc[-1])
                        metrics["equity_max"] = float(profits.max())
                        metrics["equity_min"] = float(profits.min())
                
                break
        except Exception as e:
            metrics["errors"].append(f"trades: {e}")
    
    # Look for equity exports
    equity_csvs = list(folder.glob("*equity*.csv")) + list(folder.glob("*equity*.json"))
    for csv_path in equity_csvs:
        try:
            df = pd.read_csv(csv_path)
            if "Equity" in df.columns or "equity" in df.columns:
                metrics["has_equity"] = True
                equity_col = "Equity" if "Equity" in df.columns else "equity"
                metrics["equity_max"] = float(df[equity_col].max())
                metrics["equity_min"] = float(df[equity_col].min())
                break
        except Exception as e:
            metrics["errors"].append(f"equity: {e}")
    
    # Look for signals
    signal_csvs = list(folder.glob("*signal*.csv"))
    if signal_csvs:
        metrics["has_signals"] = True
    
    return metrics


def main():
    print("=" * 70)
    print(f"TV Export Batch Analysis")
    print(f"Source: {TV_EXPORT_DIR}")
    print(f"Output: {OUTPUT_DIR}")
    print("=" * 70)
    
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    
    # Get all exported strategy folders
    folders = sorted([f for f in TV_EXPORT_DIR.iterdir() if f.is_dir()])
    
    if LIMIT_STRATEGIES:
        folders = folders[:LIMIT_STRATEGIES]
        print(f"\nLIMIT: Processing first {LIMIT_STRATEGIES} (set LIMIT_STRATEGIES=None for all)\n")
    
    print(f"Found {len(folders)} exported folders")
    
    results = []
    stats = {"total": 0, "strategy": 0, "indicator": 0, "unknown": 0,
             "with_trades": 0, "with_equity": 0, "with_chart": 0}
    
    for i, folder in enumerate(folders, 1):
        name = folder.name
        stats["total"] += 1
        
        if i % 50 == 0 or i == 1:
            print(f"[{i}/{len(folders)}] Processing: {name}")
        
        try:
            metrics = extract_tv_metrics(folder)
            metrics["folder"] = name
            
            if metrics["type"] == "strategy":
                stats["strategy"] += 1
            elif metrics["type"] == "indicator":
                stats["indicator"] += 1
            else:
                stats["unknown"] += 1
            
            if metrics["has_trades"]:
                stats["with_trades"] += 1
            if metrics["has_equity"]:
                stats["with_equity"] += 1
            if metrics["has_chart"]:
                stats["with_chart"] += 1
            
            results.append(metrics)
            
            # Save individual result
            result_folder = OUTPUT_DIR / name
            result_folder.mkdir(exist_ok=True)
            with open(result_folder / "tv_metrics.json", "w") as f:
                json.dump(metrics, f, indent=2)
                
        except Exception as e:
            print(f"  ❌ Error: {e}")
            results.append({"folder": name, "error": str(e)})
    
    # Save summary
    summary = {
        "date": datetime.now().isoformat(),
        "stats": stats,
        "results": results,
    }
    
    summary_path = OUTPUT_DIR / f"summary_{REPORT_DATE}.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    # Save CSV summary
    df_results = pd.DataFrame(results)
    csv_path = OUTPUT_DIR / f"summary_{REPORT_DATE}.csv"
    df_results.to_csv(csv_path, index=False)
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total folders: {stats['total']}")
    print(f"  Strategies: {stats['strategy']}")
    print(f"  Indicators: {stats['indicator']}")
    print(f"  Unknown: {stats['unknown']}")
    print(f"\nWith trades: {stats['with_trades']}")
    print(f"With equity: {stats['with_equity']}")
    print(f"With chart: {stats['with_chart']}")
    print(f"\nSaved to: {summary_path}")
    print(f"Saved CSV: {csv_path}")
    
    # Show strategies with trades
    strategies_with_trades = [r for r in results if r.get("has_trades") and r.get("type") == "strategy"]
    print(f"\n--- Strategies with trades: {len(strategies_with_trades)} ---")
    for r in strategies_with_trades[:10]:
        print(f"  {r['folder']}: {r.get('trade_count', 0)} trades")


if __name__ == "__main__":
    main()
