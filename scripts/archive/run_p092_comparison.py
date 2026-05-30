# DEPRECATED: archived 2026-05-30 — see scripts/archive/README.md
#!/usr/bin/env python3
"""Run P092 comparison - TV equity vs OP equity after accessor fixes."""
import sys
sys.path.insert(0, '[local-home]/pinelib')
sys.path.insert(0, '[local-home]/backtest_engine')
sys.path.insert(0, '[local-home]/marketdata-provider')

import pandas as pd
from pathlib import Path
from datetime import datetime

TV_TRADES_CSV = "[local-home]/[workspace-root]/repo-root/reports/op_tv_007/public_tv_export_pack/tradingview_trade_exports_pack/downloaded/slvpilen_finance_simulator__list_of_trades_btc_future.csv"
OUTPUT_DIR = Path("[local-home]/[workspace-root]/repo-root/reports/op_tv_007/qty_rounding_library_run_post_accessors")

def main():
    print("=== P092 Qty Rounding Comparison (Post Accessor Fixes) ===\n")
    
    # Load TV trades
    df = pd.read_csv(TV_TRADES_CSV)
    print(f"TV trades CSV: {len(df)} rows")
    print(f"Columns: {list(df.columns)}")
    
    # Parse trades
    # Format: Trade #, Type, Signal, Date/Time, Price, Contracts, Profit USD, ...
    entries = df[df['Type'].str.contains('Entry', na=False)].copy()
    exits = df[df['Type'].str.contains('Exit', na=False)].copy()
    
    print(f"\nEntry trades: {len(entries)}")
    print(f"Exit trades: {len(exits)}")
    
    # Calculate cumulative equity from TV trades
    # TV columns: Trade #, Type, Signal, Date/Time, Price, Contracts, Profit USD, ...
    tv_equity = []
    cum_profit = 0
    initial_capital = 100000  # Example - need to check actual from TV
    
    for _, row in df.iterrows():
        if 'Exit' in str(row['Type']):
            profit = float(row['Profit USD'])
            cum_profit += profit
            tv_equity.append({
                'time': row['Date/Time'],
                'trade': row['Trade #'],
                'type': row['Type'],
                'profit': profit,
                'cum_profit': cum_profit,
                'equity': initial_capital + cum_profit
            })
    
    tv_df = pd.DataFrame(tv_equity)
    print(f"\nTV Equity from trades:")
    print(f"  First equity: {tv_df['equity'].iloc[0] if len(tv_df) > 0 else 'N/A'}")
    print(f"  Last equity: {tv_df['equity'].iloc[-1] if len(tv_df) > 0 else 'N/A'}")
    print(f"  Max equity: {tv_df['equity'].max() if len(tv_df) > 0 else 'N/A'}")
    print(f"  Min equity: {tv_df['equity'].min() if len(tv_df) > 0 else 'N/A'}")
    
    print("\n" + "="*60)
    print("Note: Full comparison requires TV chart data + strategy source.")
    print("This shows TV equity from trades CSV only.")
    print("="*60)
    
    # Save summary
    OUTPUT_DIR.mkdir(exist_ok=True)
    summary_path = OUTPUT_DIR / "p092_post_fixes_summary.csv"
    tv_df.to_csv(summary_path, index=False)
    print(f"\nSaved TV equity to: {summary_path}")
    
    # Compare with previous P092 if exists
    prev_summary = Path("[local-home]/[workspace-root]/repo-root/reports/op_tv_007/qty_rounding_library_run/summary_commission055.csv")
    if prev_summary.exists():
        prev_df = pd.read_csv(prev_summary)
        print("\nPrevious P092 summary (before fixes):")
        print(prev_df.to_string())

if __name__ == "__main__":
    main()
