# DEPRECATED: archived 2026-05-30 — see scripts/archive/README.md
#!/usr/bin/env python3
"""Full batch backtest on TV exports with commission data."""
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

import pandas as pd

from pinelib import Bar, PineRuntime, RuntimeConfig, StrategyContext, SymbolInfo, TimeframeInfo, run_generated_strategy
from pine2ast.api import parse_code, ParseOptions
from ast2python import Translator


TV_EXPORT_DIR = Path("[local-home]/[workspace-root]/workspace/pine_oracle_500_tv_exports_20260512_222247/exported")
OUTPUT_DIR = Path("[local-home]/[workspace-root]/repo-root/reports/op_tv_batch_full")
REPORT_DATE = datetime.now().strftime("%Y%m%d_%H%M%S")


def parse_pine(pine_code: str) -> str:
    try:
        options = ParseOptions(strict_v6=False)
        result = parse_code(pine_code, options)
        if result.diagnostics and any(d.severity.value >= 2 for d in result.diagnostics):
            raise RuntimeError(f"Parse errors: {result.diagnostics[:3]}")
        translator = Translator()
        translated = translator.translate_program(result.ast)
        return translated.source
    except Exception as e:
        raise RuntimeError(f"Parse error: {e}")


def load_chart(csv_path: Path) -> list[Bar]:
    df = pd.read_csv(csv_path)
    
    if df["time"].max() > 2_000_000_000_000:
        time_col = df["time"] / 1000
    elif df["time"].max() > 2_000_000_000:
        time_col = df["time"]
    else:
        time_col = df["time"]
    
    td_seconds = int(time_col.diff().dropna().mode().iloc[0])
    
    bars = []
    for idx in df.index:
        bars.append(Bar(
            time=int(time_col.iloc[idx] * 1000),
            open=float(df["open"].iloc[idx]),
            high=float(df["high"].iloc[idx]),
            low=float(df["low"].iloc[idx]),
            close=float(df["close"].iloc[idx]),
            volume=float(df.get("volume", pd.Series([0])).iloc[idx]),
            time_close=int(time_col.iloc[idx] * 1000 + td_seconds * 1000),
        ))
    return bars


def load_tv_trades(folder: Path) -> dict:
    trade_csvs = list(folder.glob("*trades*.csv"))
    for csv_path in trade_csvs:
        df = pd.read_csv(csv_path)
        if "Type" in df.columns:
            return {
                "df": df,
                "has_commission": "Commission" in df.columns,
                "total_trades": len(df),
            }
    return {}


def run_backtest(python_code: str, bars: list[Bar], commission_value: float = 0.055) -> dict:
    try:
        strategy_ctx = StrategyContext(
            commission_type="percent_per_value",
            commission_value=commission_value,
            initial_capital=10000.0,
        )
        
        runtime = PineRuntime(
            symbol=SymbolInfo("BINANCE:BTCUSDT", mintick=0.01),
            timeframe=TimeframeInfo.from_string("15m"),
            config=RuntimeConfig(max_recalculations_per_bar=16),
        )
        
        local_ns = {}
        exec(python_code, local_ns)
        strategy_class_name = [k for k in local_ns.keys() if not k.startswith('_')][0]
        strategy_class = local_ns[strategy_class_name]
        strategy_instance = strategy_class()
        
        run_generated_strategy(strategy_instance, runtime, strategy_ctx, bars)
        
        metrics = {
            "closed_trades": strategy_ctx.closedtrades,
            "open_trades": strategy_ctx.opentrades,
            "equity": strategy_ctx.equity,
            "net_profit": strategy_ctx.net_profit,
            "commission_total": 0.0,
            "trades": [],
        }
        
        if strategy_ctx.closedtrades > 0:
            for i in range(min(strategy_ctx.closedtrades, 100)):
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
                    }
                    metrics["trades"].append(trade)
                    metrics["commission_total"] += trade["commission"]
                except:
                    break
        
        return {"status": "success", "metrics": metrics}
    except Exception as e:
        return {"status": "error", "error": str(e)}


def main():
    print("=" * 70)
    print("TV Export Batch Backtest - Full Comparison")
    print("=" * 70)
    
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    
    all_folders = sorted([f for f in TV_EXPORT_DIR.iterdir() if f.is_dir()])
    print(f"Total folders: {len(all_folders)}")
    
    # Find strategies with trades
    print("\nPass 1: Finding strategies with trades...")
    strategies_with_trades = []
    for i, folder in enumerate(all_folders, 1):
        tv_trades = load_tv_trades(folder)
        if tv_trades and tv_trades.get("total_trades", 0) > 0:
            strategies_with_trades.append((folder, tv_trades))
    
    print(f"Found {len(strategies_with_trades)} strategies with trades")
    
    # Test on subset first
    test_limit = 20
    print(f"\nPass 2: Running backtests on first {test_limit} strategies...")
    
    results = []
    stats = {"success": 0, "parse_error": 0, "backtest_error": 0, "no_chart": 0}
    
    for i, (folder, tv_trades) in enumerate(strategies_with_trades[:test_limit], 1):
        name = folder.name
        print(f"[{i}/{test_limit}] {name}")
        
        result = {"folder": name, "status": "pending"}
        
        pine_files = list(folder.glob("*.pine"))
        if not pine_files:
            result["status"] = "no_pine"
            stats["no_chart"] += 1
            results.append(result)
            continue
        
        try:
            python_code = parse_pine(pine_files[0].read_text())
        except Exception as e:
            result["status"] = "parse_error"
            result["error"] = str(e)
            stats["parse_error"] += 1
            results.append(result)
            continue
        
        chart_csvs = list(folder.glob("tv_*.csv"))
        if not chart_csvs:
            result["status"] = "no_chart"
            stats["no_chart"] += 1
            results.append(result)
            continue
        
        try:
            bars = load_chart(chart_csvs[0])
        except Exception as e:
            result["status"] = "chart_error"
            result["error"] = str(e)
            results.append(result)
            continue
        
        backtest_result = run_backtest(python_code, bars)
        
        if backtest_result["status"] == "success":
            result["status"] = "success"
            result["metrics"] = backtest_result["metrics"]
            stats["success"] += 1
            print(f"  ✅ {backtest_result['metrics']['closed_trades']} trades, commission={backtest_result['metrics'].get('commission_total', 0):.4f}")
        else:
            result["status"] = "backtest_error"
            result["error"] = backtest_result.get("error")
            stats["backtest_error"] += 1
            print(f"  ❌ {backtest_result.get('error', 'unknown')[:50]}")
        
        results.append(result)
        
        result_folder = OUTPUT_DIR / name
        result_folder.mkdir(exist_ok=True)
        with open(result_folder / "result.json", "w") as f:
            json.dump(result, f, indent=2, default=str)
    
    summary = {
        "date": datetime.now().isoformat(),
        "test_limit": test_limit,
        "total_with_trades": len(strategies_with_trades),
        "stats": stats,
        "results": results,
    }
    
    summary_path = OUTPUT_DIR / f"summary_{REPORT_DATE}.json"
    with open(summary_path, "w") as f:
        json.dump(summary, f, indent=2, default=str)
    
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"Tested: {test_limit} strategies")
    print(f"Success: {stats['success']}")
    print(f"Parse errors: {stats['parse_error']}")
    print(f"Backtest errors: {stats['backtest_error']}")
    print(f"Saved to: {summary_path}")


if __name__ == "__main__":
    main()
