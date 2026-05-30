# DEPRECATED: archived 2026-05-30 — see scripts/archive/README.md
#!/usr/bin/env python3
"""Extract and compare TV trade data for strategies with trades."""
from __future__ import annotations

import sys
sys.path.insert(0, '[local-home]/pinelib')

import json
from datetime import datetime
from pathlib import Path

import pandas as pd


# ── CONFIG ──────────────────────────────────────────────────────────
TV_EXPORT_DIR = Path("[local-home]/[workspace-root]/workspace/pine_oracle_500_tv_exports_20260512_222247/exported")
OUTPUT_DIR = Path("[local-home]/[workspace-root]/repo-root/reports/op_tv_batch_trades")
REPORT_DATE = datetime.now().strftime("%Y%m%d_%H%M%S")


def extract_trade_details(folder: Path) -> dict:
    """Extract detailed trade info from TV export."""
    result = {
        "folder": folder.name,
        "has_trades": False,
        "entries": [],
        "exits": [],
        "stats": {},
    }
    
    # Find trades CSV
    trade_csvs = list(folder.glob("*trades*.csv"))
    for csv_path in trade_csvs:
        try:
            df = pd.read_csv(csv_path)
            if "Trade #" not in df.columns and "Type" not in df.columns:
                continue
            
            result["has_trades"] = True
            
            # Get entry trades
            if "Type" in df.columns:
                entries = df[df["Type"].str.contains("Entry", na=False)]
                exits = df[df["Type"].str.contains("Exit", na=False)]
                
                result["entries"] = len(entries)
                result["exits"] = len(exits)
                
                # Extract detailed info for first few trades
                if len(df) > 0:
                    first_trade = df.iloc[0]
                    result["stats"] = {
                        "first_trade_num": int(first_trade.get("Trade #", 0)),
                        "first_type": str(first_trade.get("Type", "")),
                        "first_direction": str(first_trade.get("Signal", "")),
                        "first_profit": float(first_trade.get("Profit USD", 0)),
                        "first_commission": float(first_trade.get("Commission", 0)) if "Commission" in df.columns else None,
                    }
                    
                    # Commission stats
                    if "Commission" in df.columns:
                        commissions = df["Commission"].dropna()
                        if len(commissions) > 0:
                            result["stats"]["total_commission"] = float(commissions.sum())
                            result["stats"]["avg_commission"] = float(commissions.mean())
                    
                    # Profit stats
                    if "Profit USD" in df.columns:
                        profits = df["Profit USD"].dropna()
                        if len(profits) > 0:
                            result["stats"]["total_profit"] = float(profits.sum())
                            result["stats"]["avg_profit"] = float(profits.mean())
                            result["stats"]["max_profit"] = float(profits.max())
                            result["stats"]["min_profit"] = float(profits.min())
                            result["stats"]["win_rate"] = float((profits > 0).sum()) / len(profits)
                
                break
        except Exception as e:
            result["error"] = str(e)
    
    return result


def main():
    print("=" * 70)
    print("Extracting TV Trade Details for Strategies")
    print("=" * 70)
    
    OUTPUT_DIR.mkdir(exist_ok=True, parents=True)
    
    # Get all folders
    all_folders = sorted([f for f in TV_EXPORT_DIR.iterdir() if f.is_dir()])
    print(f"Total folders: {len(all_folders)}")
    
    results = []
    
    for i, folder in enumerate(all_folders, 1):
        if i % 100 == 0 or i == 1:
            print(f"[{i}/{len(all_folders)}] {folder.name}")
        
        try:
            details = extract_trade_details(folder)
            if details["has_trades"]:
                results.append(details)
                
                # Save individual result
                result_folder = OUTPUT_DIR / folder.name
                result_folder.mkdir(exist_ok=True)
                with open(result_folder / "tv_trades.json", "w") as f:
                    json.dump(details, f, indent=2)
        except Exception as e:
            print(f"  ❌ Error: {e}")
    
    print(f"\nFound {len(results)} strategies with trades")
    
    # Create summary DataFrame
    if results:
        df = pd.DataFrame(results)
        
        # Stats summary
        print("\n--- Commission Stats ---")
        commissions = [r["stats"].get("total_commission") for r in results if r["stats"].get("total_commission") is not None]
        if commissions:
            print(f"Strategies with commission: {len(commissions)}")
            print(f"Avg commission: {sum(commissions)/len(commissions):.4f}")
        
        print("\n--- Profit Stats ---")
        profits = [r["stats"].get("total_profit") for r in results if r["stats"].get("total_profit") is not None]
        if profits:
            print(f"Strategies with profit: {len(profits)}")
            print(f"Avg total profit: {sum(profits)/len(profits):.2f}")
        
        # Save summary
        summary_path = OUTPUT_DIR / f"strategies_with_trades_{REPORT_DATE}.json"
        with open(summary_path, "w") as f:
            json.dump(results, f, indent=2, default=str)
        print(f"\nSaved to: {summary_path}")
        
        # Save CSV
        csv_path = OUTPUT_DIR / f"strategies_with_trades_{REPORT_DATE}.csv"
        df.to_csv(csv_path, index=False)
        print(f"Saved CSV to: {csv_path}")
    
    print("\n✅ Done!")


if __name__ == "__main__":
    main()
