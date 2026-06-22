#!/usr/bin/env python3
"""Memory-safe candles-only LINK/AVAX multi-timeframe scan.

Processes one symbol/timeframe at a time so the RPi does not OOM on the full
5m/15m/1h/4h panel matrix. Uses only kline-derived price/volume features.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import gc
import math
import sys
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path('/home/moltbot1/.openclaw/workspace/openpine/research/microstructure-v2')
CACHE = ROOT / 'data/cache'
ART = ROOT / 'artifacts/link_avax_fast_candles_scan'
ART.mkdir(parents=True, exist_ok=True)

FEE_RATE = 0.00055
SLIPPAGE = 0.00020
COST_RT = 2 * (FEE_RATE + SLIPPAGE)
EPS = 1e-12
SYMBOLS = ['AVAXUSDT', 'LINKUSDT']
TIMEFRAMES = {
    '5m': '5min',
    '15m': '15min',
    '1h': '1h',
    '4h': '4h',
}
HORIZONS = {
    '5m': [4, 8, 16, 32],
    '15m': [2, 4, 8, 16],
    '1h': [2, 4, 8, 12],
    '4h': [1, 2, 3, 4],
}
MAX_HOLD_GRID = {
    '5m': [8, 16, 32],
    '15m': [4, 8, 16],
    '1h': [2, 4, 8],
    '4h': [1, 2, 4],
}
TP_GRID = [0.75, 1.0, 1.25, 1.5]
SL_GRID = [1.0, 1.25, 1.5, 2.0]


@dataclass(frozen=True)
class Candidate:
    symbol: str
    timeframe: str
    direction: int
    name: str
    mask: np.ndarray


def wilson_lower(wins: int, n: int, z: float = 1.959963984540054) -> float:
    if n <= 0:
        return math.nan
    p = wins / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (center - margin) / denom


def equity_stats(r: np.ndarray, split: str) -> dict[str, object]:
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return {'split': split, 'n': 0}
    wins = r[r > 0]
    losses = r[r <= 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    win_count = int((r > 0).sum())
    return {
        'split': split,
        'n': int(len(r)),
        'wr': float(win_count / len(r)),
        'wilson95': float(wilson_lower(win_count, len(r))),
        'mean_r': float(np.mean(r)),
        'median_r': float(np.median(r)),
        'profit_factor': float(gross_win / gross_loss) if gross_loss else math.inf,
        'max_dd_r': float(dd.min()) if len(dd) else 0.0,
        'p10_r': float(np.quantile(r, 0.10)),
        'p05_r': float(np.quantile(r, 0.05)),
        'avg_win_r': float(np.mean(wins)) if len(wins) else 0.0,
        'avg_loss_r': float(np.mean(losses)) if len(losses) else 0.0,
    }


def read_kline(symbol: str) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for p in sorted((CACHE / symbol).glob('kline_*.parquet')):
        try:
            df = pd.read_parquet(p, columns=['open', 'high', 'low', 'close', 'volume', 'turnover'])
        except Exception as exc:
            # Some pre-launch Bybit chunks are valid empty parquet files without
            # OHLC columns; skip them rather than failing the full scan.
            print(f'WARN skip {p.name}: {exc}', flush=True)
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        return pd.DataFrame()
    out = pd.concat(frames, copy=False).sort_index()
    out = out[~out.index.duplicated(keep='last')]
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True)).as_unit('ns')
    out.index.name = 'timestamp'
    return out


def rsi_wilder(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / (avg_loss + EPS)
    return 100 - 100 / (1 + rs)


def build_panel(k1: pd.DataFrame, tf: str) -> pd.DataFrame:
    rule = TIMEFRAMES[tf]
    df = k1.resample(rule, origin='epoch', label='left', closed='left').agg(
        open=('open', 'first'),
        high=('high', 'max'),
        low=('low', 'min'),
        close=('close', 'last'),
        volume=('volume', 'sum'),
        turnover=('turnover', 'sum'),
    ).dropna(subset=['open', 'high', 'low', 'close'])
    close = df['close']
    for n in [7, 14]:
        df[f'rsi{n}'] = rsi_wilder(close, n)
    lower = (close < close.shift(1)).astype(int)
    upper = (close > close.shift(1)).astype(int)
    for w in [3, 4, 5]:
        df[f'clc{w}'] = lower.rolling(w, min_periods=w).sum()
        df[f'cuc{w}'] = upper.rolling(w, min_periods=w).sum()
    tr = pd.concat([
        df['high'] - df['low'],
        (df['high'] - df['close'].shift(1)).abs(),
        (df['low'] - df['close'].shift(1)).abs(),
    ], axis=1).max(axis=1)
    df['atr_pct'] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / (close + EPS)
    df['vol_sma20'] = df['volume'].rolling(20, min_periods=10).mean()
    df['vol_spike'] = df['volume'] / (df['vol_sma20'] + EPS)
    df['ema96'] = close.ewm(span=96, adjust=False, min_periods=48).mean()
    df['hour'] = df.index.hour
    df['session'] = df['hour'] // 4
    df['atr_q'] = pd.qcut(df['atr_pct'].rank(method='first'), 5, labels=False, duplicates='drop')
    df['trend96'] = np.where(close >= df['ema96'], 1, -1)
    return df.dropna(subset=['rsi7', 'rsi14', 'atr_pct'])


def session_mask(df: pd.DataFrame, sess: object) -> np.ndarray:
    if sess == 'ALL':
        return np.ones(len(df), dtype=bool)
    if isinstance(sess, str) and sess.startswith('H'):
        return (df['hour'].to_numpy(int) == int(sess[1:]))
    return (df['session'].to_numpy(int) == int(sess))


def make_candidates(symbol: str, tf: str, df: pd.DataFrame) -> list[Candidate]:
    sessions: list[object] = ['ALL', 0, 1, 2, 3, 4, 5, 'H05']
    vol15 = (df['vol_spike'].to_numpy(float) >= 1.5)
    atr_not_top = (df['atr_q'].fillna(2).to_numpy(float) <= 3)
    below = (df['trend96'].to_numpy(int) < 0)
    above = (df['trend96'].to_numpy(int) > 0)
    filters_long = {'none': None, 'vol15': vol15, 'belowE96': below, 'atrNotTop': atr_not_top}
    filters_short = {'none': None, 'vol15': vol15, 'aboveE96': above, 'atrNotTop': atr_not_top}
    cands: list[Candidate] = []
    for rsi_col in ['rsi7', 'rsi14']:
        rsi = df[rsi_col].to_numpy(float)
        for thr in [20, 25, 30, 35]:
            for sess in sessions:
                sm = session_mask(df, sess)
                for fname, fm in filters_long.items():
                    mask = (rsi <= thr) & sm
                    if fm is not None:
                        mask &= fm
                    cands.append(Candidate(symbol, tf, 1, f'LONG_{rsi_col}<={thr}_S{sess}_{fname}', mask))
        for thr in [65, 70, 75, 80]:
            for sess in sessions:
                sm = session_mask(df, sess)
                for fname, fm in filters_short.items():
                    mask = (rsi >= thr) & sm
                    if fm is not None:
                        mask &= fm
                    cands.append(Candidate(symbol, tf, -1, f'SHORT_{rsi_col}>={thr}_S{sess}_{fname}', mask))
    for w in [3, 4, 5]:
        clc = df[f'clc{w}'].to_numpy(float)
        cuc = df[f'cuc{w}'].to_numpy(float)
        for sess in sessions:
            sm = session_mask(df, sess)
            cands.append(Candidate(symbol, tf, 1, f'LONG_CLC{w}_S{sess}_none', (clc == w) & sm))
            cands.append(Candidate(symbol, tf, -1, f'SHORT_CUC{w}_S{sess}_none', (cuc == w) & sm))
    return cands


def split_masks(entry_pos: np.ndarray, n_bars: int) -> dict[str, np.ndarray]:
    frac = entry_pos / max(n_bars, 1)
    return {
        'train': frac < 0.60,
        'validation': (frac >= 0.60) & (frac < 0.80),
        'holdout': frac >= 0.80,
        'all': np.ones(len(entry_pos), dtype=bool),
    }


def quick_rows_for_candidate(df: pd.DataFrame, cand: Candidate) -> tuple[list[dict[str, object]], dict[str, np.ndarray]]:
    n = len(df)
    max_h = max(HORIZONS[cand.timeframe])
    idx = np.flatnonzero(cand.mask)
    idx = idx[idx + 1 + max_h < n]
    if len(idx) < 20:
        return [], {}
    entry = idx + 1
    close = df['close'].to_numpy(float)
    atr = np.maximum(df['atr_pct'].to_numpy(float), 0.003)
    entry_price = close[entry]
    risk = atr[entry]
    split = split_masks(entry, n)
    rows: list[dict[str, object]] = []
    for h in HORIZONS[cand.timeframe]:
        gross = cand.direction * (close[entry + h] / entry_price - 1.0)
        r = (gross - COST_RT) / risk
        base = {
            'symbol': cand.symbol,
            'timeframe': cand.timeframe,
            'candidate': cand.name,
            'direction': cand.direction,
            'horizon': h,
        }
        for name, m in split.items():
            st = equity_stats(r[m], name)
            for k, v in st.items():
                if k != 'split':
                    base[f'{name}_{k}'] = v
        rows.append(base)
    return rows, {'idx': idx, 'entry': entry, 'risk': risk, 'entry_price': entry_price}


def simulate_path(df: pd.DataFrame, cand: Candidate, idx: np.ndarray, entry: np.ndarray, risk: np.ndarray, entry_price: np.ndarray, tp: float, sl: float, max_hold: int) -> pd.DataFrame:
    high = df['high'].to_numpy(float)
    low = df['low'].to_numpy(float)
    close = df['close'].to_numpy(float)
    n = len(df)
    ok = entry + max_hold < n
    idx = idx[ok]
    entry = entry[ok]
    risk = risk[ok]
    entry_price = entry_price[ok]
    if len(entry) == 0:
        return pd.DataFrame()
    outcomes = np.empty(len(entry), dtype=float)
    exit_reason = np.full(len(entry), 'timeout', dtype=object)
    for i, ep in enumerate(entry):
        eprice = entry_price[i]
        rr = risk[i]
        if cand.direction > 0:
            tp_price = eprice * (1 + tp * rr)
            sl_price = eprice * (1 - sl * rr)
        else:
            tp_price = eprice * (1 - tp * rr)
            sl_price = eprice * (1 + sl * rr)
        outcome = None
        reason = 'timeout'
        for pos in range(ep, ep + max_hold + 1):
            if cand.direction > 0:
                hit_tp = high[pos] >= tp_price
                hit_sl = low[pos] <= sl_price
            else:
                hit_tp = low[pos] <= tp_price
                hit_sl = high[pos] >= sl_price
            if hit_tp and hit_sl:
                outcome = -sl
                reason = 'same_bar_sl'
                break
            if hit_sl:
                outcome = -sl
                reason = 'sl'
                break
            if hit_tp:
                outcome = tp
                reason = 'tp'
                break
        if outcome is None:
            gross = cand.direction * (close[ep + max_hold] / eprice - 1.0)
            outcome = (gross - COST_RT) / rr
        outcomes[i] = outcome
        exit_reason[i] = reason
    frac = entry / max(n, 1)
    splits = np.where(frac < 0.60, 'train', np.where(frac < 0.80, 'validation', 'holdout'))
    return pd.DataFrame({
        'symbol': cand.symbol,
        'timeframe': cand.timeframe,
        'candidate': cand.name,
        'direction': cand.direction,
        'signal_ts': df.index[idx].astype(str),
        'entry_ts': df.index[entry].astype(str),
        'entry_pos': entry,
        'tp_r': tp,
        'sl_r': sl,
        'max_hold': max_hold,
        'split': splits,
        'outcome_r': outcomes,
        'win': outcomes > 0,
        'exit_reason': exit_reason,
        'hour': df['hour'].to_numpy(int)[idx],
        'session': df['session'].to_numpy(int)[idx],
        'atr_q': df['atr_q'].fillna(-1).to_numpy(int)[idx],
        'vol_spike': df['vol_spike'].to_numpy(float)[idx],
    })


def summarize_path(tr: pd.DataFrame, cand: Candidate, tp: float, sl: float, max_hold: int) -> dict[str, object]:
    base: dict[str, object] = {
        'symbol': cand.symbol,
        'timeframe': cand.timeframe,
        'candidate': cand.name,
        'direction': cand.direction,
        'tp_r': tp,
        'sl_r': sl,
        'max_hold': max_hold,
    }
    for split in ['train', 'validation', 'holdout', 'all']:
        part = tr if split == 'all' else tr[tr['split'] == split]
        st = equity_stats(part['outcome_r'].to_numpy(float), split)
        for k, v in st.items():
            if k != 'split':
                base[f'{split}_{k}'] = v
        if split == 'all':
            base['all_tp_rate'] = float((part['exit_reason'] == 'tp').mean()) if len(part) else math.nan
            base['all_sl_rate'] = float(part['exit_reason'].isin(['sl', 'same_bar_sl']).mean()) if len(part) else math.nan
    base['rank_score'] = (
        float(base.get('validation_mean_r', -999) or -999) * 0.35
        + float(base.get('holdout_mean_r', -999) or -999) * 0.45
        + float(base.get('all_mean_r', -999) or -999) * 0.20
        + min(float(base.get('holdout_profit_factor', 0) or 0), 10.0) * 0.02
    )
    return base


def df_to_md(df: pd.DataFrame, max_rows: int = 20) -> str:
    if df.empty:
        return '_empty_'
    view = df.head(max_rows).copy()
    cols = list(view.columns)
    rows = []
    for _, r in view.iterrows():
        row = []
        for c in cols:
            v = r[c]
            if isinstance(v, float):
                row.append(f'{v:.6g}')
            else:
                row.append(str(v))
        rows.append(row)
    widths = [len(c) for c in cols]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    lines = ['| ' + ' | '.join(c.ljust(widths[i]) for i, c in enumerate(cols)) + ' |']
    lines.append('| ' + ' | '.join('-' * w for w in widths) + ' |')
    lines.extend('| ' + ' | '.join(row[i].ljust(widths[i]) for i in range(len(cols))) + ' |' for row in rows)
    return '\n'.join(lines)


def main() -> int:
    coverage_rows: list[dict[str, object]] = []
    quick_rows: list[dict[str, object]] = []
    path_rows: list[dict[str, object]] = []
    best_trades: pd.DataFrame | None = None
    best_score = -1e18

    for symbol in SYMBOLS:
        print(f'load {symbol}', flush=True)
        k1 = read_kline(symbol)
        coverage_rows.append({'symbol': symbol, 'source': 'kline_1m', 'rows': len(k1), 'start': str(k1.index.min()), 'end': str(k1.index.max())})
        for tf in TIMEFRAMES:
            panel = build_panel(k1, tf)
            print(f'panel {symbol} {tf}: {len(panel)} bars {panel.index.min()} -> {panel.index.max()}', flush=True)
            cands = make_candidates(symbol, tf, panel)
            cand_meta: dict[tuple[str, int], tuple[Candidate, dict[str, np.ndarray]]] = {}
            local_quick: list[dict[str, object]] = []
            for cand in cands:
                rows, meta = quick_rows_for_candidate(panel, cand)
                if not rows:
                    continue
                local_quick.extend(rows)
                cand_meta[(cand.name, cand.direction)] = (cand, meta)
            qdf = pd.DataFrame(local_quick)
            if qdf.empty:
                del panel
                gc.collect()
                continue
            qdf['rank_score'] = (
                qdf['validation_mean_r'].fillna(-999) * 0.35
                + qdf['holdout_mean_r'].fillna(-999) * 0.45
                + qdf['all_mean_r'].fillna(-999) * 0.20
            )
            qdf = qdf.sort_values(['rank_score', 'holdout_n', 'holdout_wr'], ascending=[False, False, False])
            quick_rows.extend(qdf.head(80).to_dict('records'))
            top_unique = qdf.drop_duplicates(['candidate', 'direction']).head(12)
            for _, qr in top_unique.iterrows():
                cand, meta = cand_meta[(str(qr['candidate']), int(qr['direction']))]
                for tp in TP_GRID:
                    for sl in SL_GRID:
                        for hold in MAX_HOLD_GRID[tf]:
                            tr = simulate_path(panel, cand, meta['idx'], meta['entry'], meta['risk'], meta['entry_price'], tp, sl, hold)
                            if tr.empty:
                                continue
                            row = summarize_path(tr, cand, tp, sl, hold)
                            path_rows.append(row)
                            score = float(row['rank_score'])
                            if score > best_score:
                                best_score = score
                                best_trades = tr
            del panel, qdf, cand_meta
            gc.collect()
        del k1
        gc.collect()

    coverage = pd.DataFrame(coverage_rows)
    quick = pd.DataFrame(quick_rows)
    path = pd.DataFrame(path_rows)
    if not quick.empty:
        quick = quick.sort_values(['rank_score', 'holdout_n', 'holdout_wr'], ascending=[False, False, False])
    if not path.empty:
        path = path.sort_values(['rank_score', 'holdout_n', 'holdout_wr'], ascending=[False, False, False])
    coverage.to_csv(ART / 'coverage.csv', index=False)
    quick.to_csv(ART / 'quick_scan_results.csv', index=False)
    path.to_csv(ART / 'path_grid_results.csv', index=False)
    if best_trades is not None:
        best_trades.to_csv(ART / 'best_candidate_trades.csv', index=False)

    strict = pd.DataFrame()
    if not path.empty:
        strict = path[
            (path['holdout_n'] >= 500)
            & (path['holdout_wr'] >= 0.60)
            & (path['holdout_mean_r'] > 0)
            & (path['validation_mean_r'] > 0)
            & (path['holdout_profit_factor'] > 1.10)
            & (path['validation_profit_factor'] > 1.10)
        ].copy()
    lines = [
        '# LINK/AVAX fast candles-only scan',
        '',
        'Memory-safe scan: one symbol/timeframe at a time. Uses Bybit Linear kline-derived price/volume only; no partial tradeflow/OI/funding filters.',
        '',
        f'- strict deploy-gate candidates: {len(strict)}',
        '- strict gate: holdout n>=500, holdout WR>=60%, validation+holdout meanR>0, validation+holdout PF>1.10',
        '',
        '## Coverage',
        '',
        df_to_md(coverage, 20),
        '',
        '## Top path-grid rows',
        '',
        df_to_md(path.head(30) if not path.empty else path, 30),
        '',
        '## Top quick close-to-close rows',
        '',
        df_to_md(quick.head(30) if not quick.empty else quick, 30),
        '',
        '## Strict rows',
        '',
        df_to_md(strict.head(30) if not strict.empty else strict, 30),
    ]
    (ART / 'FAST_CANDLES_SCAN_REPORT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'REPORT {ART / "FAST_CANDLES_SCAN_REPORT.md"}', flush=True)
    if not path.empty:
        print(path.head(20).to_string(index=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
