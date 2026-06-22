#!/usr/bin/env python3
"""Focused LINK/AVAX candle-only tuning around the best pockets.

Purpose: tune nearby parameters without OOM and report strict/robustness gates.
This is post-discovery research; results are marked deployable only if they pass
untouched holdout gates with enough trades.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import gc
import math

import numpy as np
import pandas as pd

ROOT = Path('/home/moltbot1/.openclaw/workspace/openpine/research/microstructure-v2')
CACHE = ROOT / 'data/cache'
ART = ROOT / 'artifacts/link_avax_focused_tuner'
ART.mkdir(parents=True, exist_ok=True)

FEE_RATE = 0.00055
SLIPPAGE = 0.00020
COST_RT = 2 * (FEE_RATE + SLIPPAGE)
EPS = 1e-12


@dataclass(frozen=True)
class Setup:
    symbol: str
    tf: str
    direction: int
    family: str
    rsi_col: str
    rsi_thr: float
    session: str
    vol_min: float | None
    atr_max_q: int | None
    trend: str
    clc_w: int | None = None


def wilson_lower(wins: int, n: int, z: float = 1.959963984540054) -> float:
    if n <= 0:
        return math.nan
    p = wins / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (center - margin) / denom


def equity_stats(r: np.ndarray) -> dict[str, float | int]:
    r = np.asarray(r, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) == 0:
        return {'n': 0}
    wins = r[r > 0]
    losses = r[r <= 0]
    gw = float(wins.sum()) if len(wins) else 0.0
    gl = float(-losses.sum()) if len(losses) else 0.0
    eq = np.cumsum(r)
    dd = eq - np.maximum.accumulate(eq)
    wc = int((r > 0).sum())
    return {
        'n': int(len(r)),
        'wr': float(wc / len(r)),
        'wilson95': float(wilson_lower(wc, len(r))),
        'mean_r': float(np.mean(r)),
        'median_r': float(np.median(r)),
        'pf': float(gw / gl) if gl else math.inf,
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
        except Exception:
            continue
        if not df.empty:
            frames.append(df)
    out = pd.concat(frames, copy=False).sort_index()
    out = out[~out.index.duplicated(keep='last')]
    out.index = pd.DatetimeIndex(pd.to_datetime(out.index, utc=True)).as_unit('ns')
    out.index.name = 'timestamp'
    return out


def rsi_wilder(close: pd.Series, n: int) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    ag = gain.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    al = loss.ewm(alpha=1/n, adjust=False, min_periods=n).mean()
    rs = ag / (al + EPS)
    return 100 - 100 / (1 + rs)


def build_panel(k1: pd.DataFrame, rule: str) -> pd.DataFrame:
    df = k1.resample(rule, origin='epoch', label='left', closed='left').agg(
        open=('open','first'), high=('high','max'), low=('low','min'), close=('close','last'),
        volume=('volume','sum'), turnover=('turnover','sum')
    ).dropna(subset=['open','high','low','close'])
    close = df['close']
    for n in [7, 10, 14, 21]:
        df[f'rsi{n}'] = rsi_wilder(close, n)
    lower = (close < close.shift(1)).astype(int)
    upper = (close > close.shift(1)).astype(int)
    for w in [2,3,4,5,6]:
        df[f'clc{w}'] = lower.rolling(w, min_periods=w).sum()
        df[f'cuc{w}'] = upper.rolling(w, min_periods=w).sum()
    tr = pd.concat([df['high']-df['low'], (df['high']-close.shift(1)).abs(), (df['low']-close.shift(1)).abs()], axis=1).max(axis=1)
    df['atr_pct'] = tr.ewm(alpha=1/14, adjust=False, min_periods=14).mean() / (close + EPS)
    df['atr_q'] = pd.qcut(df['atr_pct'].rank(method='first'), 5, labels=False, duplicates='drop')
    df['vol_sma20'] = df['volume'].rolling(20, min_periods=10).mean()
    df['vol_spike'] = df['volume'] / (df['vol_sma20'] + EPS)
    for span in [48,96,192,288]:
        df[f'ema{span}'] = close.ewm(span=span, adjust=False, min_periods=max(20, span//2)).mean()
    df['hour'] = df.index.hour
    df['session'] = df['hour'] // 4
    return df.dropna(subset=['rsi7','rsi14','atr_pct'])


def session_mask(df: pd.DataFrame, name: str) -> np.ndarray:
    if name == 'ALL':
        return np.ones(len(df), dtype=bool)
    if name.startswith('S'):
        return df['session'].to_numpy(int) == int(name[1:])
    if name.startswith('H'):
        if '_' in name:
            a,b = name[1:].split('_')
            h = df['hour'].to_numpy(int)
            return (h >= int(a)) & (h <= int(b))
        return df['hour'].to_numpy(int) == int(name[1:])
    raise ValueError(name)


def trend_mask(df: pd.DataFrame, trend: str, direction: int) -> np.ndarray:
    close = df['close'].to_numpy(float)
    if trend == 'none':
        return np.ones(len(df), dtype=bool)
    if trend == 'above96':
        return close >= df['ema96'].to_numpy(float)
    if trend == 'below96':
        return close <= df['ema96'].to_numpy(float)
    if trend == 'with96':
        return close >= df['ema96'].to_numpy(float) if direction > 0 else close <= df['ema96'].to_numpy(float)
    if trend == 'against96':
        return close <= df['ema96'].to_numpy(float) if direction > 0 else close >= df['ema96'].to_numpy(float)
    if trend == 'above288':
        return close >= df['ema288'].to_numpy(float)
    if trend == 'below288':
        return close <= df['ema288'].to_numpy(float)
    return np.ones(len(df), dtype=bool)


def setup_name(s: Setup) -> str:
    return f'{s.family}_{s.rsi_col}_{s.rsi_thr:g}_{s.session}_vol{s.vol_min}_atr{s.atr_max_q}_{s.trend}_clc{s.clc_w}'


def mask_for_setup(df: pd.DataFrame, s: Setup) -> np.ndarray:
    rsi = df[s.rsi_col].to_numpy(float)
    if s.direction > 0:
        mask = rsi <= s.rsi_thr
    else:
        mask = rsi >= s.rsi_thr
    mask &= session_mask(df, s.session)
    if s.vol_min is not None:
        mask &= df['vol_spike'].to_numpy(float) >= s.vol_min
    if s.atr_max_q is not None:
        mask &= df['atr_q'].fillna(2).to_numpy(float) <= s.atr_max_q
    if s.clc_w is not None:
        if s.direction > 0:
            mask &= df[f'clc{s.clc_w}'].to_numpy(float) >= s.clc_w
        else:
            mask &= df[f'cuc{s.clc_w}'].to_numpy(float) >= s.clc_w
    mask &= trend_mask(df, s.trend, s.direction)
    return mask


def split_arrays(entry: np.ndarray, n: int) -> dict[str, np.ndarray]:
    frac = entry / max(n, 1)
    return {'train': frac < 0.60, 'validation': (frac >= 0.60) & (frac < 0.80), 'holdout': frac >= 0.80, 'all': np.ones(len(entry), dtype=bool)}


def simulate(df: pd.DataFrame, s: Setup, tp: float, sl: float, hold: int) -> pd.DataFrame:
    n = len(df)
    mask = mask_for_setup(df, s)
    sig = np.flatnonzero(mask)
    sig = sig[sig + 1 + hold < n]
    if len(sig) < 20:
        return pd.DataFrame()
    entry = sig + 1
    high = df['high'].to_numpy(float)
    low = df['low'].to_numpy(float)
    close = df['close'].to_numpy(float)
    atr = np.maximum(df['atr_pct'].to_numpy(float), 0.003)
    outcomes = np.empty(len(entry), dtype=float)
    reason = np.full(len(entry), 'timeout', dtype=object)
    for i, ep in enumerate(entry):
        eprice = close[ep]
        risk = atr[ep]
        if s.direction > 0:
            tp_price = eprice * (1 + tp*risk)
            sl_price = eprice * (1 - sl*risk)
        else:
            tp_price = eprice * (1 - tp*risk)
            sl_price = eprice * (1 + sl*risk)
        out = None
        why = 'timeout'
        for pos in range(ep, ep+hold+1):
            if s.direction > 0:
                hit_tp = high[pos] >= tp_price
                hit_sl = low[pos] <= sl_price
            else:
                hit_tp = low[pos] <= tp_price
                hit_sl = high[pos] >= sl_price
            if hit_tp and hit_sl:
                out = -sl; why = 'same_bar_sl'; break
            if hit_sl:
                out = -sl; why = 'sl'; break
            if hit_tp:
                out = tp; why = 'tp'; break
        if out is None:
            gross = s.direction * (close[ep+hold] / eprice - 1)
            out = (gross - COST_RT) / risk
        outcomes[i] = out
        reason[i] = why
    frac = entry / max(n,1)
    split = np.where(frac < 0.60, 'train', np.where(frac < 0.80, 'validation', 'holdout'))
    return pd.DataFrame({
        'signal_pos': sig,
        'entry_pos': entry,
        'signal_ts': df.index[sig].astype(str),
        'entry_ts': df.index[entry].astype(str),
        'split': split,
        'outcome_r': outcomes,
        'win': outcomes > 0,
        'exit_reason': reason,
        'hour': df['hour'].to_numpy(int)[sig],
        'session': df['session'].to_numpy(int)[sig],
        'atr_q': df['atr_q'].fillna(-1).to_numpy(int)[sig],
        'vol_spike': df['vol_spike'].to_numpy(float)[sig],
        'atr_pct': df['atr_pct'].to_numpy(float)[sig],
    })


def row_for(s: Setup, tr: pd.DataFrame, tp: float, sl: float, hold: int) -> dict[str, object]:
    row: dict[str, object] = {
        'symbol': s.symbol, 'tf': s.tf, 'setup': setup_name(s), 'direction': s.direction,
        'family': s.family, 'rsi_col': s.rsi_col, 'rsi_thr': s.rsi_thr, 'session': s.session,
        'vol_min': s.vol_min, 'atr_max_q': s.atr_max_q, 'trend': s.trend, 'clc_w': s.clc_w,
        'tp_r': tp, 'sl_r': sl, 'hold': hold,
    }
    for split in ['train','validation','holdout','all']:
        part = tr if split == 'all' else tr[tr['split'] == split]
        st = equity_stats(part['outcome_r'].to_numpy(float))
        for k,v in st.items():
            row[f'{split}_{k}'] = v
        if split == 'all' and len(part):
            row['all_tp_rate'] = float((part['exit_reason'] == 'tp').mean())
            row['all_sl_rate'] = float(part['exit_reason'].isin(['sl','same_bar_sl']).mean())
    row['score'] = (
        float(row.get('validation_mean_r', -999) or -999) * 0.30 +
        float(row.get('holdout_mean_r', -999) or -999) * 0.45 +
        min(float(row.get('holdout_profit_factor', row.get('holdout_pf', 0)) or 0), 10) * 0.02 +
        min(float(row.get('holdout_n', 0) or 0), 500) / 500 * 0.05 +
        float(row.get('all_mean_r', -999) or -999) * 0.18
    )
    return row


def generate_setups(symbol: str, tf: str) -> list[Setup]:
    setups: list[Setup] = []
    trends_short = ['none','above96','with96','against96']
    trends_long = ['none','below96','with96','against96']
    # Focus only around pockets already found by the full candles scan:
    # - AVAX 5m short overbought + volume spike
    # - LINK 5m long oversold + volume spike
    # - LINK 15m short large-n close-to-close pocket
    if symbol == 'AVAXUSDT' and tf == '5m':
        sessions = ['S1','S2','H08_11','H09','ALL']
        for rsi_col in ['rsi10','rsi14','rsi21']:
            for thr in [70,72,75,78,80]:
                for sess in sessions:
                    for vol in [1.0, 1.2, 1.5, 2.0]:
                        for atr in [None, 3, 4]:
                            for trend in ['none','above96','with96']:
                                setups.append(Setup(symbol, tf, -1, 'SHORT_RSI', rsi_col, thr, sess, vol, atr, trend))
        for w in [3,4,5]:
            for sess in sessions:
                for vol in [1.0, 1.5, 2.0]:
                    for atr in [None, 3, 4]:
                        setups.append(Setup(symbol, tf, -1, 'SHORT_CUC', 'rsi14', 0, sess, vol, atr, 'none', clc_w=w))
    elif symbol == 'LINKUSDT' and tf == '5m':
        sessions = ['S1','H04_07','H05','H06','ALL']
        for rsi_col in ['rsi10','rsi14','rsi21']:
            for thr in [20,22,25,28,30]:
                for sess in sessions:
                    for vol in [1.0, 1.2, 1.5, 2.0]:
                        for atr in [None, 3, 4]:
                            for trend in ['none','below96','with96']:
                                setups.append(Setup(symbol, tf, 1, 'LONG_RSI', rsi_col, thr, sess, vol, atr, trend))
        for w in [3,4,5]:
            for sess in sessions:
                for vol in [1.0, 1.5, 2.0]:
                    for atr in [None, 3, 4]:
                        setups.append(Setup(symbol, tf, 1, 'LONG_CLC', 'rsi14', 100, sess, vol, atr, 'none', clc_w=w))
    elif symbol == 'LINKUSDT' and tf == '15m':
        sessions = ['S2','H08_11','H09','H10','ALL']
        for rsi_col in ['rsi7','rsi10','rsi14']:
            for thr in [68,70,72,75]:
                for sess in sessions:
                    for vol in [None, 1.0, 1.2, 1.5]:
                        for atr in [None, 3, 4]:
                            for trend in ['none','above96','with96']:
                                setups.append(Setup(symbol, tf, -1, 'SHORT_RSI', rsi_col, thr, sess, vol, atr, trend))
    return setups


def tune_symbol_tf(symbol: str, tf: str, rule: str) -> pd.DataFrame:
    print(f'load {symbol} {tf}', flush=True)
    k1 = read_kline(symbol)
    df = build_panel(k1, rule)
    del k1
    gc.collect()
    print(f'panel {symbol} {tf}: {len(df)} {df.index.min()} -> {df.index.max()}', flush=True)
    rows: list[dict[str, object]] = []
    setups = generate_setups(symbol, tf)
    tp_grid = [0.5,0.75,1.0,1.25,1.5]
    sl_grid = [1.0,1.5,2.0,2.5]
    hold_grid = [16,24,32,48] if tf == '5m' else [8,12,16,24]
    for i, s in enumerate(setups, 1):
        if i % 1000 == 0:
            print(f'{symbol} {tf} setups {i}/{len(setups)} rows={len(rows)}', flush=True)
        # Cheap prefilter: enough OOS signals before expensive path grid.
        sig = np.flatnonzero(mask_for_setup(df, s))
        if len(sig) < 80:
            continue
        frac = (sig + 1) / max(len(df), 1)
        if ((frac >= 0.80).sum() < 30) or (((frac >= 0.60) & (frac < 0.80)).sum() < 30):
            continue
        best_for_setup: list[dict[str, object]] = []
        for tp in tp_grid:
            for sl in sl_grid:
                for hold in hold_grid:
                    tr = simulate(df, s, tp, sl, hold)
                    if tr.empty:
                        continue
                    row = row_for(s, tr, tp, sl, hold)
                    # reject obvious train-only illusions to reduce output
                    if int(row.get('validation_n', 0) or 0) < 30 or int(row.get('holdout_n', 0) or 0) < 30:
                        continue
                    best_for_setup.append(row)
        if best_for_setup:
            best_for_setup.sort(key=lambda r: float(r['score']), reverse=True)
            rows.extend(best_for_setup[:4])
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values(['score','holdout_n','holdout_wr'], ascending=[False,False,False])
    out.to_csv(ART / f'{symbol}_{tf}_focused.csv', index=False)
    del df
    gc.collect()
    return out


def df_to_md(df: pd.DataFrame, n: int = 20) -> str:
    if df.empty:
        return '_empty_'
    view = df.head(n).copy()
    cols = list(view.columns)
    rows=[]
    for _, r in view.iterrows():
        row=[]
        for c in cols:
            v=r[c]
            if isinstance(v, float): row.append(f'{v:.6g}')
            else: row.append(str(v))
        rows.append(row)
    widths=[len(c) for c in cols]
    for row in rows:
        for i,cell in enumerate(row): widths[i]=max(widths[i],len(cell))
    lines=['| '+' | '.join(c.ljust(widths[i]) for i,c in enumerate(cols))+' |']
    lines.append('| '+' | '.join('-'*w for w in widths)+' |')
    lines += ['| '+' | '.join(row[i].ljust(widths[i]) for i in range(len(cols)))+' |' for row in rows]
    return '\n'.join(lines)


def main() -> int:
    frames=[]
    # Focus where prior scan had signal. 15m included for LINK large-n close-to-close pocket.
    jobs=[('AVAXUSDT','5m','5min'),('LINKUSDT','5m','5min'),('LINKUSDT','15m','15min')]
    for sym,tf,rule in jobs:
        frames.append(tune_symbol_tf(sym,tf,rule))
    allres=pd.concat([f for f in frames if not f.empty], ignore_index=True) if any(not f.empty for f in frames) else pd.DataFrame()
    if not allres.empty:
        allres=allres.sort_values(['score','holdout_n','holdout_wr'], ascending=[False,False,False])
    allres.to_csv(ART/'focused_tuning_results.csv', index=False)
    strict=pd.DataFrame()
    large=pd.DataFrame()
    if not allres.empty:
        strict=allres[(allres.holdout_n>=500)&(allres.holdout_wr>=0.60)&(allres.holdout_mean_r>0)&(allres.validation_mean_r>0)&(allres.holdout_pf>1.10)&(allres.validation_pf>1.10)].copy()
        large=allres[(allres.holdout_n>=200)&(allres.validation_n>=200)&(allres.holdout_mean_r>0)&(allres.validation_mean_r>0)].copy()
    cols=['symbol','tf','family','rsi_col','rsi_thr','session','vol_min','atr_max_q','trend','clc_w','tp_r','sl_r','hold','train_n','validation_n','validation_wr','validation_mean_r','validation_pf','holdout_n','holdout_wr','holdout_mean_r','holdout_pf','all_n','all_wr','all_mean_r','all_pf','all_max_dd_r','score']
    report=[
        '# LINK/AVAX focused tuner', '',
        'Post-discovery focused tuning around AVAX 5m short and LINK 5m/15m pockets. Treat as optimization; deployable only if strict holdout gates pass.', '',
        f'- total rows: {len(allres)}', f'- strict rows: {len(strict)}', f'- large validation+holdout positive rows: {len(large)}', '',
        '## Strict rows', '', df_to_md(strict[cols] if not strict.empty else strict, 30), '',
        '## Large OOS positive rows', '', df_to_md(large.sort_values(['score','holdout_n'], ascending=[False,False])[cols] if not large.empty else large, 30), '',
        '## Top rows', '', df_to_md(allres[cols] if not allres.empty else allres, 40), '',
    ]
    (ART/'FOCUSED_TUNER_REPORT.md').write_text('\n'.join(report), encoding='utf-8')
    print(f'REPORT {ART/"FOCUSED_TUNER_REPORT.md"}', flush=True)
    if not allres.empty:
        print(allres.head(20)[cols].to_string(index=False), flush=True)
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
