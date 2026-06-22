#!/usr/bin/env python3
"""Pilot-test the LINKUSDT 15m short RSI pocket with honest execution.

The focused tuner found a promising LINK 15m short pocket, but its path simulator
was deliberately quick. This pilot removes the optimistic assumptions:
- signal is known at signal bar close;
- entry is next bar open, not next bar close;
- ATR/risk is taken from the signal bar, not the future entry bar;
- round-trip fees+slippage are subtracted for TP/SL exits too, not only timeout exits.

It reports exact-candidate metrics, cost sensitivity, nearby parameter/filter
sensitivity, and drawdown drivers.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import math
from typing import Iterable

import numpy as np
import pandas as pd

from link_avax_focused_tuner import build_panel, read_kline

ROOT = Path('/home/moltbot1/.openclaw/workspace/openpine/research/microstructure-v2')
ART = ROOT / 'artifacts/link15_short_pocket_pilot'
ART.mkdir(parents=True, exist_ok=True)

FEE_RATE = 0.00055
SLIPPAGE = 0.00020
BASE_COST_RT = 2 * (FEE_RATE + SLIPPAGE)
EPS = 1e-12


@dataclass(frozen=True)
class Rule:
    rsi_col: str = 'rsi10'
    rsi_thr: float = 70.0
    session: str = 'H09'
    vol_min: float | None = None
    vol_max: float | None = None
    atr_max_q: int | None = None
    trend: str = 'none'
    tp_r: float = 1.0
    sl_r: float = 2.5
    hold: int = 16

    def name(self) -> str:
        return (
            f'{self.rsi_col}>={self.rsi_thr:g}_{self.session}_'
            f'volmin{self.vol_min}_volmax{self.vol_max}_atrmax{self.atr_max_q}_'
            f'{self.trend}_tp{self.tp_r:g}_sl{self.sl_r:g}_h{self.hold}'
        )


def wilson_lower(wins: int, n: int, z: float = 1.959963984540054) -> float:
    if n <= 0:
        return math.nan
    p = wins / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (center - margin) / denom


def stats(r: Iterable[float]) -> dict[str, float | int]:
    arr = np.asarray(list(r), dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return {'n': 0}
    wins = arr[arr > 0]
    losses = arr[arr <= 0]
    gw = float(wins.sum()) if len(wins) else 0.0
    gl = float(-losses.sum()) if len(losses) else 0.0
    eq = np.cumsum(arr)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    wc = int((arr > 0).sum())
    return {
        'n': int(len(arr)),
        'wr': float(wc / len(arr)),
        'wilson95': float(wilson_lower(wc, len(arr))),
        'mean_r': float(arr.mean()),
        'median_r': float(np.median(arr)),
        'pf': float(gw / gl) if gl else math.inf,
        'max_dd_r': float(dd.min()) if len(dd) else 0.0,
        'p10_r': float(np.quantile(arr, 0.10)),
        'p05_r': float(np.quantile(arr, 0.05)),
        'avg_win_r': float(wins.mean()) if len(wins) else 0.0,
        'avg_loss_r': float(losses.mean()) if len(losses) else 0.0,
    }


def session_mask(df: pd.DataFrame, session: str) -> np.ndarray:
    hour = df.index.hour.to_numpy()
    sess = hour // 4
    if session == 'ALL':
        return np.ones(len(df), dtype=bool)
    if session.startswith('S'):
        return sess == int(session[1:])
    if session.startswith('H') and '_' in session:
        lo, hi = session[1:].split('_')
        return (hour >= int(lo)) & (hour <= int(hi))
    if session.startswith('H'):
        return hour == int(session[1:])
    raise ValueError(session)


def trend_mask(df: pd.DataFrame, trend: str) -> np.ndarray:
    close = df['close'].to_numpy(float)
    if trend == 'none':
        return np.ones(len(df), dtype=bool)
    if trend == 'above96':
        return close >= df['ema96'].to_numpy(float)
    if trend == 'below96':
        return close <= df['ema96'].to_numpy(float)
    if trend == 'above288':
        return close >= df['ema288'].to_numpy(float)
    if trend == 'below288':
        return close <= df['ema288'].to_numpy(float)
    raise ValueError(trend)


def signal_mask(df: pd.DataFrame, rule: Rule) -> np.ndarray:
    mask = df[rule.rsi_col].to_numpy(float) >= rule.rsi_thr
    mask &= session_mask(df, rule.session)
    if rule.vol_min is not None:
        mask &= df['vol_spike'].to_numpy(float) >= rule.vol_min
    if rule.vol_max is not None:
        mask &= df['vol_spike'].to_numpy(float) <= rule.vol_max
    if rule.atr_max_q is not None:
        mask &= df['atr_q'].fillna(2).to_numpy(float) <= rule.atr_max_q
    mask &= trend_mask(df, rule.trend)
    return mask


def split_name(entry_pos: np.ndarray, n: int) -> np.ndarray:
    frac = entry_pos / max(n, 1)
    return np.where(frac < 0.60, 'train', np.where(frac < 0.80, 'validation', 'holdout'))


def simulate_rule(df: pd.DataFrame, rule: Rule, cost_mult: float = 1.0, legacy_optimistic: bool = False) -> pd.DataFrame:
    """Simulate short-only rule.

    Honest mode: signal at bar N close, enter at N+1 open, risk uses bar N ATR,
    costs apply to every exit.
    Legacy mode approximates the old quick tuner assumptions for comparison.
    """
    n = len(df)
    sig = np.flatnonzero(signal_mask(df, rule))
    sig = sig[sig + 1 + rule.hold < n]
    if len(sig) == 0:
        return pd.DataFrame()
    entry = sig + 1
    open_ = df['open'].to_numpy(float)
    high = df['high'].to_numpy(float)
    low = df['low'].to_numpy(float)
    close = df['close'].to_numpy(float)
    atr = np.maximum(df['atr_pct'].to_numpy(float), 0.003)
    risk = atr[entry if legacy_optimistic else sig]
    entry_price = close[entry] if legacy_optimistic else open_[entry]
    cost_rt = BASE_COST_RT * cost_mult
    outcomes = np.empty(len(entry), dtype=float)
    gross_returns = np.empty(len(entry), dtype=float)
    cost_r = cost_rt / np.maximum(risk, EPS)
    exit_price = np.empty(len(entry), dtype=float)
    reason = np.full(len(entry), 'timeout', dtype=object)
    mae_r = np.empty(len(entry), dtype=float)
    mfe_r = np.empty(len(entry), dtype=float)

    for i, ep in enumerate(entry):
        e = entry_price[i]
        rr = risk[i]
        tp_price = e * (1 - rule.tp_r * rr)  # short target
        sl_price = e * (1 + rule.sl_r * rr)  # short stop
        out_gross: float | None = None
        xp = close[ep + rule.hold]
        why = 'timeout'
        local_high = high[ep: ep + rule.hold + 1]
        local_low = low[ep: ep + rule.hold + 1]
        mae_r[i] = float(np.max((local_high / e - 1.0) / rr))
        mfe_r[i] = float(np.max((1.0 - local_low / e) / rr))
        for pos in range(ep, ep + rule.hold + 1):
            hit_tp = low[pos] <= tp_price
            hit_sl = high[pos] >= sl_price
            if hit_tp and hit_sl:
                xp = sl_price
                out_gross = -(xp / e - 1.0)  # short gross
                why = 'same_bar_sl'
                break
            if hit_sl:
                xp = sl_price
                out_gross = -(xp / e - 1.0)
                why = 'sl'
                break
            if hit_tp:
                xp = tp_price
                out_gross = -(xp / e - 1.0)
                why = 'tp'
                break
        if out_gross is None:
            out_gross = -(xp / e - 1.0)
        gross_returns[i] = out_gross
        exit_price[i] = xp
        if legacy_optimistic and why in {'tp', 'sl', 'same_bar_sl'}:
            # Replicate old quick-tuner convention: bracket exits returned exact R
            # and did not subtract fee/slippage. Kept only for diagnosing the gap.
            outcomes[i] = rule.tp_r if why == 'tp' else -rule.sl_r
        else:
            outcomes[i] = (out_gross - cost_rt) / rr
        reason[i] = why

    tr = pd.DataFrame({
        'signal_pos': sig,
        'entry_pos': entry,
        'signal_ts': df.index[sig],
        'entry_ts': df.index[entry],
        'split': split_name(entry, n),
        'entry_price': entry_price,
        'risk_atr_pct': risk,
        'cost_r': cost_r,
        'gross_return': gross_returns,
        'outcome_r': outcomes,
        'win': outcomes > 0,
        'exit_price': exit_price,
        'exit_reason': reason,
        'rsi10': df['rsi10'].to_numpy(float)[sig],
        'rsi14': df['rsi14'].to_numpy(float)[sig],
        'vol_spike': df['vol_spike'].to_numpy(float)[sig],
        'atr_q': df['atr_q'].fillna(-1).to_numpy(int)[sig],
        'atr_pct': df['atr_pct'].to_numpy(float)[sig],
        'close_above_ema96': (df['close'].to_numpy(float)[sig] >= df['ema96'].to_numpy(float)[sig]),
        'close_above_ema288': (df['close'].to_numpy(float)[sig] >= df['ema288'].to_numpy(float)[sig]),
        'mae_r': mae_r,
        'mfe_r': mfe_r,
    })
    signal_ts_naive = tr['signal_ts'].dt.tz_convert(None)
    tr['year'] = signal_ts_naive.dt.year
    tr['month'] = signal_ts_naive.dt.to_period('M').astype(str)
    tr['halfyear'] = signal_ts_naive.dt.year.astype(str) + 'H' + np.where(signal_ts_naive.dt.month <= 6, '1', '2')
    return tr


def summary_row(name: str, rule: Rule, tr: pd.DataFrame, cost_mult: float, legacy: bool = False) -> dict[str, object]:
    row: dict[str, object] = {'name': name, 'rule': rule.name(), 'cost_mult': cost_mult, 'legacy': legacy}
    for split in ['train', 'validation', 'holdout', 'all']:
        part = tr if split == 'all' else tr[tr['split'] == split]
        st = stats(part['outcome_r'].to_numpy(float))
        for k, v in st.items():
            row[f'{split}_{k}'] = v
    if len(tr):
        row['tp_rate'] = float((tr['exit_reason'] == 'tp').mean())
        row['sl_rate'] = float(tr['exit_reason'].isin(['sl', 'same_bar_sl']).mean())
        row['timeout_rate'] = float((tr['exit_reason'] == 'timeout').mean())
        row['avg_cost_r'] = float(tr['cost_r'].mean())
        row['median_cost_r'] = float(tr['cost_r'].median())
    return row


def summarize_group(tr: pd.DataFrame, by: str) -> pd.DataFrame:
    rows = []
    for key, part in tr.groupby(by, dropna=False, sort=True):
        st = stats(part['outcome_r'])
        st['bucket'] = str(key)
        st['sum_r'] = float(part['outcome_r'].sum())
        rows.append(st)
    if not rows:
        return pd.DataFrame()
    out = pd.DataFrame(rows)
    return out[['bucket','n','wr','mean_r','pf','sum_r','max_dd_r','p10_r','p05_r']].sort_values('bucket')


def max_drawdown_window(tr: pd.DataFrame) -> dict[str, object]:
    if tr.empty:
        return {}
    eq = tr['outcome_r'].cumsum().to_numpy(float)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    trough_i = int(np.argmin(dd))
    peak_i = int(np.argmax(eq[:trough_i+1])) if trough_i >= 0 else 0
    after = np.where(eq[trough_i:] >= peak[trough_i])[0]
    recovery_i = int(trough_i + after[0]) if len(after) else None
    window = tr.iloc[peak_i:trough_i+1]
    return {
        'peak_ts': str(tr.iloc[peak_i]['entry_ts']),
        'trough_ts': str(tr.iloc[trough_i]['entry_ts']),
        'recovery_ts': str(tr.iloc[recovery_i]['entry_ts']) if recovery_i is not None else None,
        'dd_r': float(dd[trough_i]),
        'trades': int(len(window)),
        'losses': int((window['outcome_r'] <= 0).sum()),
        'wins': int((window['outcome_r'] > 0).sum()),
        'sum_r': float(window['outcome_r'].sum()),
    }


def streaks(tr: pd.DataFrame) -> dict[str, int]:
    max_loss = cur_loss = 0
    max_win = cur_win = 0
    for x in tr['outcome_r'].to_numpy(float):
        if x > 0:
            cur_win += 1
            cur_loss = 0
        else:
            cur_loss += 1
            cur_win = 0
        max_win = max(max_win, cur_win)
        max_loss = max(max_loss, cur_loss)
    return {'max_win_streak': max_win, 'max_loss_streak': max_loss}


def fmt_table(df: pd.DataFrame, max_rows: int = 30) -> str:
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


def variant_rules() -> list[tuple[str, Rule]]:
    out: list[tuple[str, Rule]] = []
    # Exact candidate announced to user.
    out.append(('exact_announced', Rule(rsi_col='rsi10', rsi_thr=70, session='H09', tp_r=1.0, sl_r=2.5, hold=16)))
    # Nearby knobs found in previous focused tuning.
    for thr in [68, 70, 72, 75]:
        for tp in [0.75, 1.0, 1.25, 1.5]:
            for sl in [2.0, 2.5, 3.0]:
                for hold in [8, 16, 24]:
                    out.append((f'sweep_thr{thr}_tp{tp}_sl{sl}_h{hold}', Rule(rsi_col='rsi10', rsi_thr=thr, session='H09', tp_r=tp, sl_r=sl, hold=hold)))
    # Feature filters to see if drawdown is removable without future leakage.
    for vol_min in [1.0, 1.2, 1.5, 2.0]:
        for tp, sl, hold in [(1.0, 2.5, 16), (1.25, 2.5, 16), (1.5, 2.5, 24)]:
            out.append((f'volmin{vol_min}_tp{tp}_sl{sl}_h{hold}', Rule(rsi_col='rsi10', rsi_thr=70, session='H09', vol_min=vol_min, tp_r=tp, sl_r=sl, hold=hold)))
    for vol_max in [2.0, 3.0, 5.0]:
        out.append((f'volmax{vol_max}', Rule(rsi_col='rsi10', rsi_thr=70, session='H09', vol_max=vol_max, tp_r=1.0, sl_r=2.5, hold=16)))
    for atr_max in [2, 3, 4]:
        out.append((f'atrmax{atr_max}', Rule(rsi_col='rsi10', rsi_thr=70, session='H09', atr_max_q=atr_max, tp_r=1.0, sl_r=2.5, hold=16)))
    for trend in ['above96', 'below96', 'above288', 'below288']:
        out.append((f'trend_{trend}', Rule(rsi_col='rsi10', rsi_thr=70, session='H09', trend=trend, tp_r=1.0, sl_r=2.5, hold=16)))
    # More sensitive RSI variants around the small-n high-score row.
    for rsi_col, thr in [('rsi14', 68), ('rsi14', 70), ('rsi7', 72)]:
        for vol in [None, 1.2, 1.5]:
            out.append((f'{rsi_col}_{thr}_vol{vol}', Rule(rsi_col=rsi_col, rsi_thr=thr, session='H09', vol_min=vol, tp_r=1.5, sl_r=2.5, hold=24)))
    # Deduplicate by rule name.
    seen = set()
    uniq = []
    for name, rule in out:
        key = rule.name()
        if key not in seen:
            seen.add(key)
            uniq.append((name, rule))
    return uniq


def main() -> int:
    print('LOAD LINKUSDT kline', flush=True)
    k1 = read_kline('LINKUSDT')
    panel = build_panel(k1, '15min')
    print(f'PANEL rows={len(panel)} start={panel.index.min()} end={panel.index.max()}', flush=True)

    exact_rule = Rule(rsi_col='rsi10', rsi_thr=70, session='H09', tp_r=1.0, sl_r=2.5, hold=16)
    exact = simulate_rule(panel, exact_rule, cost_mult=1.0, legacy_optimistic=False)
    legacy = simulate_rule(panel, exact_rule, cost_mult=1.0, legacy_optimistic=True)
    no_cost = simulate_rule(panel, exact_rule, cost_mult=0.0, legacy_optimistic=False)
    double_cost = simulate_rule(panel, exact_rule, cost_mult=2.0, legacy_optimistic=False)

    exact.to_csv(ART / 'exact_honest_trades.csv', index=False)
    legacy.to_csv(ART / 'exact_legacy_optimistic_trades.csv', index=False)

    summary_rows = [
        summary_row('exact_honest_cost1x', exact_rule, exact, 1.0, False),
        summary_row('exact_honest_cost0x', exact_rule, no_cost, 0.0, False),
        summary_row('exact_honest_cost2x', exact_rule, double_cost, 2.0, False),
        summary_row('exact_legacy_optimistic', exact_rule, legacy, 1.0, True),
    ]

    variant_rows = []
    top_trades: dict[str, pd.DataFrame] = {}
    for i, (name, rule) in enumerate(variant_rules(), 1):
        if i % 50 == 0:
            print(f'variants {i}', flush=True)
        tr = simulate_rule(panel, rule, cost_mult=1.0, legacy_optimistic=False)
        if tr.empty:
            continue
        row = summary_row(name, rule, tr, 1.0, False)
        row['score'] = (
            float(row.get('validation_mean_r', -999) or -999) * 0.30
            + float(row.get('holdout_mean_r', -999) or -999) * 0.45
            + float(row.get('all_mean_r', -999) or -999) * 0.20
            + min(float(row.get('holdout_n', 0) or 0), 500) / 500 * 0.05
        )
        variant_rows.append(row)
        if int(row.get('holdout_n', 0) or 0) >= 40 and float(row.get('holdout_mean_r', -999) or -999) > 0:
            top_trades[name] = tr
    summary = pd.DataFrame(summary_rows)
    variants = pd.DataFrame(variant_rows)
    variants = variants.sort_values(['score', 'holdout_n', 'holdout_wr'], ascending=[False, False, False]) if not variants.empty else variants
    summary.to_csv(ART / 'exact_summary.csv', index=False)
    variants.to_csv(ART / 'variant_summary.csv', index=False)

    if not variants.empty:
        best_name = str(variants.iloc[0]['name'])
        best_rule_name = str(variants.iloc[0]['rule'])
        # Recompute best by finding matching rule.
        best_rule = next(rule for name, rule in variant_rules() if rule.name() == best_rule_name)
        best_tr = simulate_rule(panel, best_rule, cost_mult=1.0, legacy_optimistic=False)
        best_tr.to_csv(ART / 'best_variant_trades.csv', index=False)
    else:
        best_name, best_rule_name, best_tr = '', '', pd.DataFrame()

    # Diagnostics for exact honest candidate.
    yearly = summarize_group(exact, 'year')
    halfyear = summarize_group(exact, 'halfyear')
    month = summarize_group(exact, 'month')
    atr = summarize_group(exact, 'atr_q')
    exit_reason = summarize_group(exact, 'exit_reason')
    trend96 = summarize_group(exact, 'close_above_ema96')
    trend288 = summarize_group(exact, 'close_above_ema288')
    exact['vol_bucket'] = pd.cut(exact['vol_spike'], bins=[0, 1, 1.5, 2, 3, 5, 999], include_lowest=True).astype(str)
    vol = summarize_group(exact, 'vol_bucket')
    exact['rsi_bucket'] = pd.cut(exact['rsi10'], bins=[70, 72, 75, 80, 100], include_lowest=True).astype(str)
    rsi_bucket = summarize_group(exact, 'rsi_bucket')

    for name, df in [
        ('yearly.csv', yearly), ('halfyear.csv', halfyear), ('monthly.csv', month),
        ('by_atr_q.csv', atr), ('by_exit_reason.csv', exit_reason),
        ('by_trend96.csv', trend96), ('by_trend288.csv', trend288), ('by_vol_bucket.csv', vol), ('by_rsi_bucket.csv', rsi_bucket),
    ]:
        df.to_csv(ART / name, index=False)

    dd = max_drawdown_window(exact)
    st = streaks(exact)
    strict = pd.DataFrame()
    robust100 = pd.DataFrame()
    if not variants.empty:
        strict = variants[
            (variants['holdout_n'] >= 500)
            & (variants['holdout_wr'] >= 0.60)
            & (variants['holdout_mean_r'] > 0)
            & (variants['validation_mean_r'] > 0)
            & (variants['holdout_pf'] > 1.10)
            & (variants['validation_pf'] > 1.10)
        ].copy()
        robust100 = variants[
            (variants['holdout_n'] >= 100)
            & (variants['validation_n'] >= 100)
            & (variants['holdout_mean_r'] > 0)
            & (variants['validation_mean_r'] > 0)
            & (variants['all_mean_r'] > 0)
        ].copy()

    cols = [
        'name','cost_mult','legacy','all_n','all_wr','all_mean_r','all_pf','all_max_dd_r',
        'validation_n','validation_wr','validation_mean_r','validation_pf',
        'holdout_n','holdout_wr','holdout_mean_r','holdout_pf','avg_cost_r','tp_rate','sl_rate','timeout_rate'
    ]
    vcols = [
        'name','rule','all_n','all_wr','all_mean_r','all_pf','all_max_dd_r',
        'validation_n','validation_wr','validation_mean_r','validation_pf',
        'holdout_n','holdout_wr','holdout_mean_r','holdout_pf','avg_cost_r','score'
    ]
    lines = [
        '# LINKUSDT 15m short pocket pilot', '',
        'Candidate tested: SHORT when RSI10>=70 on 09:00 UTC 15m bars; default TP=1R, SL=2.5R, max_hold=16 bars.', '',
        'Execution model for honest rows: signal bar close -> next bar open entry; ATR/risk from signal bar; round-trip fee+slippage applied to every exit.', '',
        f'- panel rows: {len(panel)}',
        f'- panel range: {panel.index.min()} -> {panel.index.max()}',
        f'- strict variants: {len(strict)}',
        f'- robust100 variants: {len(robust100)}',
        f'- best variant: {best_name} / {best_rule_name}', '',
        '## Exact candidate: honest vs no-cost vs double-cost vs legacy optimistic', '',
        fmt_table(summary[cols], 20), '',
        '## Exact candidate drawdown window', '',
        '```json', pd.Series({**dd, **st}).to_json(force_ascii=False, indent=2), '```', '',
        '## Exact yearly', '', fmt_table(yearly, 20), '',
        '## Exact half-year', '', fmt_table(halfyear, 20), '',
        '## Exact by ATR bucket', '', fmt_table(atr, 20), '',
        '## Exact by volume bucket', '', fmt_table(vol, 20), '',
        '## Exact by RSI bucket', '', fmt_table(rsi_bucket, 20), '',
        '## Exact by trend filters', '',
        '### EMA96', '', fmt_table(trend96, 10), '',
        '### EMA288', '', fmt_table(trend288, 10), '',
        '## Exact exit reasons', '', fmt_table(exit_reason, 10), '',
        '## Strict variants', '', fmt_table(strict[vcols] if not strict.empty else strict, 30), '',
        '## Robust100 variants', '', fmt_table(robust100[vcols] if not robust100.empty else robust100, 30), '',
        '## Top honest variants', '', fmt_table(variants[vcols].head(40) if not variants.empty else variants, 40), '',
    ]
    (ART / 'LINK15_SHORT_POCKET_PILOT.md').write_text('\n'.join(lines), encoding='utf-8')
    print(f'REPORT {ART / "LINK15_SHORT_POCKET_PILOT.md"}', flush=True)
    print(summary[cols].to_string(index=False), flush=True)
    print('\nTOP VARIANTS')
    if not variants.empty:
        print(variants[vcols].head(20).to_string(index=False), flush=True)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
