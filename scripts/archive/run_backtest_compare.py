# DEPRECATED: archived 2026-05-30 — see scripts/archive/README.md
#!/usr/bin/env python3
"""Run backtests on TV exported strategies and compare with TV data.

This script:
1. Loads TV exported strategy code
2. Parses PineScript via pine2ast
3. Generates Python via ast2python  
4. Runs backtest via pinelib
5. Compares with TV export data
6. Saves results
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

from pinelib import Bar, PineRuntime, RuntimeConfig, StrategyContext, SymbolInfo, TimeframeInfo, run_generated_strategy
from pine2ast.pine_parser import PineParser
from ast2python.generator import PythonGenerator


# ── CONFIG ──────────────────────────────────────────────────────────
TV_EXPORT_DIR = Path("[local-home]/[workspace-root]/workspace/pine_oracle_500_tv_exports_20260512_222247/exported")
OUTPUT_DIR = Path("[local-home]/[workspace-root]/repo-root/reports/op_tv_batch_backtest")
REPORT_DATE = datetime.now().strftime("%Y%m%d_%H%M%S")

# Limit for quick test
LIMIT: Optional[int] = 50  # None = all


def parse_pine_to_python(pine_code: str) -> str:
    """Parse PineScript and generate Python code."""
    try:
        parser = PineParser()
        ast = parser.parse(pine_code)
        generator = PythonGenerator()
        python_code = generator.generate(ast)
        return python_code
    except Exception as e:
        raise RuntimeError(f"Parse error: {e}")


def load_tv_chart(csv_path: Path) -> list[Bar]:
    """Load TV chart CSV and convert to Bar list."""
    df = pd.read_csv(csv_path)
    
    # Detect timestamp unit
    if df["time"].max() > 2_000_000_000_000:
        # milliseconds
        time_col = df["time"] / 1000
    elif df["time"].max() > 2_000_000_000:
        # seconds
        time_col = df["time"]
    else:
        # Already in some unit, assume seconds
        time_col = df["time"]
    
    # Detect timeframe from diff
    td_seconds = int(time_col.diff().dropna().mode().iloc[0])
    
    bars = []
    for _, row in df.iterrows():
        bars.append(Bar(
            time=int(time_col.iloc[_] * 1000),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row.get("volume", row.get("Volume", 0))),
            time_close=int(time_col.iloc[_] * 1000 + td_seconds * 1000),
        ))
    
    return bars


def load_tv_trades(folder: Path) -> Optional[pd.DataFrame]:
    """Load TV trades CSV if available."""
    trade_csvs = list(folder.glob("*trades*.csv"))
    for csv_path in trade_csvs:
        df = pd.read_csv(csv_path)
        if "Trade #" in df.columns or "Type" in df.columns:
            return df
    return None


def run_backtest(python_code: str, bars: list[Bar], initial_capital: float = 10000.0) -> dict:
    """Run backtest and extract metrics."""
    try:
        # Create strategy context
        strategy_ctx = StrategyContext(
            commission_type="percent_per_value",
            commission_value=0.1,
            initial_capital=initial_capital,
        )
        
        # Create runtime
        runtime = PineRuntime(
            symbol=SymbolInfo("BINANCE:BTCUSDT", mintick=0.01),
            timeframe=TimeframeInfo.from_string("15m"),
            config=RuntimeConfig(max_recalculations_per_bar=16),
        )
        
        # Execute the generated Python code to create strategy instance
        local_ns = {}
        exec(python_code, local_ns)
        
        # Get strategy class
        strategy_class_name = [k for k in local_ns.keys() if not k.startswith('_')][0]
        strategy_class = local_ns[strategy_class_name]
        strategy_instance = strategy_class()
        
        # Run backtest
        result = run_generated_strategy(strategy_instance, runtime, strategy_ctx, bars)
        
        # Extract metrics
        metrics = {
            "closed_trades": strategy_ctx.closedtrades,
            "open_trades": strategy_ctx.opentrades,
            "equity": strategy_ctx.equity,
            "net_profit": strategy_ctx.net_profit,
            "open_profit": strategy_ctx.open_profit,
            "initial_capital": strategy_ctx.initial_capital,
        }
        
        # Get trade details
        if strategy_ctx.closedtrades > 0:
            metrics["trades"] = []
            for i in range(min(strategy_ctx.closedtrades, 500)):
                try:
                    trade = {
                        "index": i,
                        "entry_price": strategy_ctx.closedtrades_entry_price(i),
                        "exit_price": strategy_ctx.closedtrades_exit_price(i),
                        "profit": strategy_ctx.closedtrades_profit(i),
                        "net_profit": strategy_ctx.closedtrades_net_profit(i),
                        "commission": strategy_ctx.closedtrades_commission(i),
                        "qty": strategy_ctx.closedtrades_qty(i),
                        "size": strategy_ctx.closedtrades_size(i),
                        "side": strategy_ctx.closedtrades_side(i),
                        "entry_id": strategy_ctx.closedtrades_entry_id(i),
                        "exit_id": strategy_ctx.closedtrades_exit_id(i),
                    }
                    metrics["trades"].append(trade)
                except Exception:
                    break
        
        return {"status": "success", "metrics": metrics}
    
    except Exception as e:
        return {"status": "error", "error": str(e)}


def compare_trades(op_trades: list, tv_trades: pd.DataFrame) -> dict:
    """Compare OpenPine trades with TV trades."""
    comparison = {
        "trade_count_match": len(op_trades) == len(tv_trades),
        "op_count": len(op_trades),
        "tv_count": len(tv_trades),
        "diffs": [],
    }
    
    return comparison


def main():
    print("=" * 70)
    print(f"TV Export Batch Backtest")
    print(f"Source: {TV_EXPORT_DIR}")
    print(f"Output: {OUTPUT_DIR}")
    print("=" * 70)
    
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    
    # Get strategy folders (those with trades)
    all_folders = sorted([f for f in TV_EXPORT_DIR.iterdir() if f.is_dir()])
    
    if LIMIT:
        all_folders = all_folders[:LIMIT]
        print(f"\nLIMIT: Processing first {LIMIT} folders\n")
    
    print(f"Found {len(all_folders)} folders")
    
    results = []
    stats = {"total": 0, "success": 0, "parse_error": 0, "backtest_error": 0, "no_chart": 0}
    
    for i, folder in enumerate(all_folders, 1):
        name = folder.name
        stats["total"] += 1
        
        if i % 10 == 0 or i == 1:
            print(f"[{i}/{len(all_folders)}] {name}")
        
        result = {"folder": name, "status": "pending"}
        
        # Find Pine file
        pine_files = list(folder.glob("*.pine"))
        if not pine_files:
            result["status"] = "no_pine"
            stats["no_chart"] += 1
            results.append(result)
            continue
        
        pine_file = pine_files[0]
        pine_code = pine_file.read_text()
        
        # Parse PineScript
        try:
            python_code = parse_pine_to_python(pine_code)
        except Exception as e:
            result["status"] = "parse_error"
            result["error"] = str(e)
            stats["parse_error"] += 1
            results.append(result)
            continue
        
        # Load TV chart
        chart_csvs = list(folder.glob("tv_*.csv"))
        if not chart_csvs:
            result["status"] = "no_chart"
            stats["no_chart"] += 1
            results.append(result)
            continue
        
        try:
            bars = load_tv_chart(chart_csvs[0])
        except Exception as e:
            result["status"] = "chart_error"
            result["error"] = str(e)
            results.append(result)
            continue
        
        # Run backtest
        backtest_result = run_backtest(python_code, bars)
        
        if backtest_result["status"] == "success":
            result["status"] = "success"
            result["metrics"] = backtest_result["metrics"]
            stats["success"] += 1
            
            # Compare with TV trades if available
            tv_trades = load_tv_trades(folder)
            if tv_trades is not None:
                result["comparison"] = compare_trades(
                    backtest_result["metrics"].get("trades", []),
                    tv_trades
                )
        else:
            result["status"] = "backtest_error"
            result["error"] = backtest_result.get("error")
            stats["backtest_error"] += 1
        
        results.append(result)
        
        # Save individual result
        result_folder = OUTPUT_DIR / name
        result_folder.mkdir(exist_ok=True)
        with open(result_folder / "result.json", "w") as f:
            json.dump(result, f, indent=2, default=str)
        
        # Save trades CSV if available
        if result["status"] == "success" and "trades" in result.get("metrics", {}):
            trades_df = pd.DataFrame(result["metrics"]["trades"])
            trades_df.to_csv(result_folder / "op_trades.csv", index=False)
    
    # Save summary
    summary = {
        "date": datetime.now().isoformat(),
        "stats": stats,
        "results": results,
    }
    
    summary_path = OUTPUT_DIR / f"summary_{REPORT_DATE}.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Total: {stats['total']}")
    print(f"Success: {stats['success']}")
    print(f"Parse errors: {stats['parse_error']}")
    print(f"Backtest errors: {stats['backtest_error']}")
    print(f"No chart: {stats['no_chart']}")
    print(f"\nSaved to: {summary_path}")


if __name__ == "__main__":
    main()
