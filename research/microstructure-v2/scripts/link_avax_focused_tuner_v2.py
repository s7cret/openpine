#!/usr/bin/env python3
"""Fast focused two-stage tuner for LINK/AVAX candles-only pockets.

Stage 1: vectorized close-to-close sweep over nearby filters.
Stage 2: conservative TP/SL path simulation only for top OOS setups.
"""
from __future__ import annotations

from pathlib import Path
import gc
import math

import numpy as np
import pandas as pd

from link_avax_focused_tuner import (
    ART as _OLD_ART,
    COST_RT,
    Setup,
    build_panel,
    df_to_md,
    equity_stats,
    mask_for_setup,
    read_kline,
    row_for,
    setup_name,
    simulate,
)

ROOT = Path('/home/moltbot1/.openclaw/workspace/openpine/research/microstructure-v2')
ART = ROOT / 'artifacts/link_avax_focused_tuner_v2'
ART.mkdir(parents=True, exist_ok=True)

JOBS = [
    ('AVAXUSDT', '5m', '5min'),
    ('LINKUSDT', '5m', '5min'),
    ('LINKUSDT', '15m', '15min'),
]
HORIZONS = {
    '5m': [8, 16, 24, 32, 48],
    '15m': [4, 8, 12, 16, 24],
}
TP_GRID = [0.5, 0.75, 1.0, 1.25, 1.5]
SL_GRID = [1.0, 1.5, 2.0, 2.5]
HOLD_GRID = {
    '5m': [16, 24, 32, 48],
    '15m': [8, 12, 16, 24],
}


def focused_setups(symbol: str, tf: str) -> list[Setup]:
    setups: list[Setup] = []
    if symbol == 'AVAXUSDT' and tf == '5m':
        for rsi_col in ['rsi10', 'rsi14', 'rsi21']:
            for thr in [72, 75, 78, 80]:
                for sess in ['S2', 'H08_11', 'H09', 'H10', 'ALL']:
                    for vol in [1.0, 1.2, 1.5, 2.0]:
                        for atr in [None, 3, 4]:
                            for trend in ['none', 'above96', 'with96']:
                                setups.append(Setup(symbol, tf, -1, 'SHORT_RSI', rsi_col, thr, sess, vol, atr, trend))
    elif symbol == 'LINKUSDT' and tf == '5m':
        for rsi_col in ['rsi10', 'rsi14', 'rsi21']:
            for thr in [22, 25, 28, 30]:
                for sess in ['S1', 'H04_07', 'H05', 'H06', 'ALL']:
                    for vol in [1.0, 1.2, 1.5, 2.0]:
                        for atr in [None, 3, 4]:
                            for trend in ['none', 'below96', 'with96']:
                                setups.append(Setup(symbol, tf, 1, 'LONG_RSI', rsi_col, thr, sess, vol, atr, trend))
    elif symbol == 'LINKUSDT' and tf == '15m':
        for rsi_col in ['rsi7', 'rsi10', 'rsi14']:
            for thr in [68, 70, 72, 75]:
                for sess in ['S2', 'H08_11', 'H09', 'H10', 'ALL']:
                    for vol in [None, 1.0, 1.2, 1.5]:
                        for atr in [None, 3, 4]:
                            for trend in ['none', 'above96', 'with96']:
                                setups.append(Setup(symbol, tf, -1, 'SHORT_RSI', rsi_col, thr, sess, vol, atr, trend))
    return setups


def split_masks(entry: np.ndarray, n: int) -> dict[str, np.ndarray]:
    frac = entry / max(n, 1)
    return {
        'train': frac < 0.60,
        'validation': (frac >= 0.60) & (frac < 0.80),
        'holdout': frac >= 0.80,
        'all': np.ones(len(entry), dtype=bool),
    }


def quick_eval(df: pd.DataFrame, s: Setup) -> list[dict[str, object]]:
    n = len(df)
    max_h = max(HORIZONS[s.tf])
    sig = np.flatnonzero(mask_for_setup(df, s))
    sig = sig[sig + 1 + max_h < n]
    if len(sig) < 40:
        return []
    entry = sig + 1
    split = split_masks(entry, n)
    if split['validation'].sum() < 25 or split['holdout'].sum() < 25:
        return []
    open_ = df['open'].to_numpy(float)
    close = df['close'].to_numpy(float)
    atr = np.maximum(df['atr_pct'].to_numpy(float), 0.003)
    entry_price = open_[entry]
    risk = atr[sig]
    rows: list[dict[str, object]] = []
    for h in HORIZONS[s.tf]:
        r = (s.direction * (close[entry + h] / entry_price - 1.0) - COST_RT) / risk
        row: dict[str, object] = {
            'symbol': s.symbol, 'tf': s.tf, 'setup': setup_name(s), 'direction': s.direction,
            'family': s.family, 'rsi_col': s.rsi_col, 'rsi_thr': s.rsi_thr, 'session': s.session,
            'vol_min': s.vol_min, 'atr_max_q': s.atr_max_q, 'trend': s.trend,
            'horizon': h,
        }
        for name, m in split.items():
            st = equity_stats(r[m])
            for k, v in st.items():
                row[f'{name}_{k}'] = v
        row['score'] = (
            float(row.get('validation_mean_r', -999) or -999) * 0.30 +
            float(row.get('holdout_mean_r', -999) or -999) * 0.45 +
            float(row.get('all_mean_r', -999) or -999) * 0.20 +
            min(float(row.get('holdout_n', 0) or 0), 500) / 500 * 0.05
        )
        rows.append(row)
    return rows


def summarize_path_for_setup(df: pd.DataFrame, s: Setup) -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for tp in TP_GRID:
        for sl in SL_GRID:
            for hold in HOLD_GRID[s.tf]:
                tr = simulate(df, s, tp, sl, hold)
                if tr.empty:
                    continue
                row = row_for(s, tr, tp, sl, hold)
                if int(row.get('validation_n', 0) or 0) < 25 or int(row.get('holdout_n', 0) or 0) < 25:
                    continue
                rows.append(row)
    return rows


def run_job(symbol: str, tf: str, rule: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    print(f'JOB {symbol} {tf}', flush=True)
    k1 = read_kline(symbol)
    df = build_panel(k1, rule)
    del k1
    gc.collect()
    print(f'panel {symbol} {tf}: {len(df)} bars {df.index.min()} -> {df.index.max()}', flush=True)
    setups = focused_setups(symbol, tf)
    quick_rows: list[dict[str, object]] = []
    for i, s in enumerate(setups, 1):
        if i % 500 == 0:
            print(f'{symbol} {tf} quick {i}/{len(setups)} rows={len(quick_rows)}', flush=True)
        quick_rows.extend(quick_eval(df, s))
    quick = pd.DataFrame(quick_rows)
    if quick.empty:
        return quick, pd.DataFrame()
    quick = quick.sort_values(['score', 'holdout_n', 'holdout_wr'], ascending=[False, False, False])
    quick.to_csv(ART / f'{symbol}_{tf}_quick.csv', index=False)

    top_setups = []
    seen = set()
    # include top score + best large-n positive rows, then path only those
    candidates = pd.concat([
        quick.head(40),
        quick[(quick['holdout_n'] >= 100) & (quick['validation_n'] >= 100) & (quick['holdout_mean_r'] > 0) & (quick['validation_mean_r'] > 0)].head(40),
    ], ignore_index=True)
    lookup = {setup_name(s): s for s in setups}
    for name in candidates['setup'].tolist():
        if name in seen or name not in lookup:
            continue
        seen.add(name)
        top_setups.append(lookup[name])
        if len(top_setups) >= 35:
            break
    path_rows: list[dict[str, object]] = []
    for i, s in enumerate(top_setups, 1):
        print(f'{symbol} {tf} path {i}/{len(top_setups)} {setup_name(s)}', flush=True)
        path_rows.extend(summarize_path_for_setup(df, s))
    path = pd.DataFrame(path_rows)
    if not path.empty:
        path = path.sort_values(['score', 'holdout_n', 'holdout_wr'], ascending=[False, False, False])
    path.to_csv(ART / f'{symbol}_{tf}_path.csv', index=False)
    del df
    gc.collect()
    return quick, path


def main() -> int:
    quick_frames = []
    path_frames = []
    for symbol, tf, rule in JOBS:
        q, p = run_job(symbol, tf, rule)
        if not q.empty:
            quick_frames.append(q)
        if not p.empty:
            path_frames.append(p)
    quick = pd.concat(quick_frames, ignore_index=True) if quick_frames else pd.DataFrame()
    path = pd.concat(path_frames, ignore_index=True) if path_frames else pd.DataFrame()
    if not quick.empty:
        quick = quick.sort_values(['score', 'holdout_n', 'holdout_wr'], ascending=[False, False, False])
    if not path.empty:
        path = path.sort_values(['score', 'holdout_n', 'holdout_wr'], ascending=[False, False, False])
    quick.to_csv(ART / 'focused_quick_results.csv', index=False)
    path.to_csv(ART / 'focused_path_results.csv', index=False)
    strict = pd.DataFrame()
    large = pd.DataFrame()
    if not path.empty:
        strict = path[
            (path['holdout_n'] >= 500) & (path['holdout_wr'] >= 0.60) &
            (path['holdout_mean_r'] > 0) & (path['validation_mean_r'] > 0) &
            (path['holdout_pf'] > 1.10) & (path['validation_pf'] > 1.10)
        ].copy()
        large = path[
            (path['holdout_n'] >= 100) & (path['validation_n'] >= 100) &
            (path['holdout_mean_r'] > 0) & (path['validation_mean_r'] > 0)
        ].copy()
    cols = ['symbol','tf','family','rsi_col','rsi_thr','session','vol_min','atr_max_q','trend','tp_r','sl_r','hold','train_n','validation_n','validation_wr','validation_mean_r','validation_pf','holdout_n','holdout_wr','holdout_mean_r','holdout_pf','all_n','all_wr','all_mean_r','all_pf','all_max_dd_r','score']
    qcols = ['symbol','tf','family','rsi_col','rsi_thr','session','vol_min','atr_max_q','trend','horizon','train_n','validation_n','validation_wr','validation_mean_r','holdout_n','holdout_wr','holdout_mean_r','all_n','all_wr','all_mean_r','score']
    lines = [
        '# LINK/AVAX focused tuner v2', '',
        'Two-stage focused tuning: close-to-close sweep then TP/SL path grid only for top setups. This is optimization; deploy only if strict holdout gates pass.', '',
        f'- quick rows: {len(quick)}', f'- path rows: {len(path)}', f'- strict path rows: {len(strict)}', f'- large positive path rows: {len(large)}', '',
        '## Strict path rows', '', df_to_md(strict[cols] if not strict.empty else strict, 30), '',
        '## Large positive path rows', '', df_to_md(large[cols].head(30) if not large.empty else large, 30), '',
        '## Top path rows', '', df_to_md(path[cols].head(40) if not path.empty else path, 40), '',
        '## Top quick rows', '', df_to_md(quick[qcols].head(40) if not quick.empty else quick, 40), '',
    ]
    (ART / 'FOCUSED_TUNER_V2_REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'REPORT {ART / "FOCUSED_TUNER_V2_REPORT.md"}', flush=True)
    if not path.empty:
        print(path[cols].head(20).to_string(index=False), flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
