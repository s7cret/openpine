#!/usr/bin/env python3
"""Multi-timeframe LINK/AVAX optimizer + drawdown rescue diagnostics.

Uses cached Bybit Linear minute-derived data. Designed for two phases:
1) broad close-to-close signal scan across TFs/params;
2) conservative TP/SL path grid + rescue filters for the best candidates.

No proprietary TV exports are used.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import argparse
import itertools
import json
import math
import sys
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path("/home/moltbot1/.openclaw/workspace/openpine/research/microstructure-v2")
CACHE = ROOT / "data" / "cache"
ART = ROOT / "artifacts" / "link_avax_multitf_optimizer"
ART.mkdir(parents=True, exist_ok=True)

FEE_RATE = 0.00055
SLIPPAGE = 0.00020
COST_RT = 2 * (FEE_RATE + SLIPPAGE)
EPS = 1e-12

TF_RULE = {
    "5m": "5min",
    "15m": "15min",
    "1h": "1h",
    "4h": "4h",
}
HORIZONS = {
    "5m": [1, 2, 4, 8, 16, 32],
    "15m": [1, 2, 4, 8, 16],
    "1h": [1, 2, 4, 8, 12],
    "4h": [1, 2, 3, 4, 6],
}
TP_GRID = [0.5, 0.75, 1.0, 1.25, 1.5, 2.0]
SL_GRID = [0.75, 1.0, 1.25, 1.5, 2.0]
MAX_HOLD_GRID = {
    "5m": [4, 8, 16, 32],
    "15m": [2, 4, 8, 16],
    "1h": [1, 2, 4, 8],
    "4h": [1, 2, 3, 4],
}


@dataclass(frozen=True)
class Candidate:
    symbol: str
    timeframe: str
    direction: int
    name: str
    mask: pd.Series


def wilson_lower(wins: int, n: int, z: float = 1.959963984540054) -> float:
    if n <= 0:
        return math.nan
    p = wins / n
    denom = 1 + z * z / n
    center = p + z * z / (2 * n)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * n)) / n)
    return (center - margin) / denom


def split_label_from_pos(pos: int, n: int) -> str:
    if n <= 0:
        return "all"
    frac = pos / n
    if frac < 0.60:
        return "train"
    if frac < 0.80:
        return "validation"
    return "holdout"


def df_to_md(df: pd.DataFrame, max_rows: int | None = None) -> str:
    if df.empty:
        return "_empty_"
    view = df.head(max_rows).copy() if max_rows else df.copy()
    def fmt(v: object) -> str:
        try:
            miss = pd.isna(v)
            if isinstance(miss, (bool, np.bool_)) and miss:
                return ""
        except Exception:
            pass
        if isinstance(v, float):
            return f"{v:.6g}"
        return str(v)
    cols = list(view.columns)
    rows = [[fmt(view.iloc[i][c]) for c in cols] for i in range(len(view))]
    widths = [len(str(c)) for c in cols]
    for row in rows:
        for i, cell in enumerate(row):
            widths[i] = max(widths[i], len(cell))
    out = ["| " + " | ".join(str(c).ljust(widths[i]) for i, c in enumerate(cols)) + " |"]
    out.append("| " + " | ".join("-" * w for w in widths) + " |")
    out.extend("| " + " | ".join(row[i].ljust(widths[i]) for i in range(len(cols))) + " |" for row in rows)
    return "\n".join(out)


def normalize_utc_ns(index: pd.Index) -> pd.DatetimeIndex:
    dt = pd.DatetimeIndex(pd.to_datetime(index, utc=True))
    if hasattr(dt, "as_unit"):
        return dt.as_unit("ns")
    return pd.DatetimeIndex(dt.astype("datetime64[ns, UTC]"))


def read_many(symbol: str, prefix: str) -> pd.DataFrame:
    files = sorted((CACHE / symbol).glob(f"{prefix}_*.parquet"))
    frames = []
    for f in files:
        try:
            df = pd.read_parquet(f)
        except Exception as exc:
            print(f"WARN read {f}: {exc}", flush=True)
            continue
        if not df.empty:
            frames.append(df)
    if not frames:
        out = pd.DataFrame()
        out.index.name = "timestamp"
        return out
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out.index = normalize_utc_ns(out.index)
    out.index.name = "timestamp"
    return out


def load_1m_sources(symbol: str) -> dict[str, pd.DataFrame]:
    return {
        "kline": read_many(symbol, "kline"),
        "tradeflow": read_many(symbol, "tradeflow_1m"),
        "oi": read_many(symbol, "oi5m"),
        "funding": read_many(symbol, "funding"),
        "mark": read_many(symbol, "mark"),
        "index": read_many(symbol, "index"),
        "premium": read_many(symbol, "premium"),
    }


def coverage_summary(symbol: str, src: dict[str, pd.DataFrame]) -> list[dict[str, object]]:
    rows = []
    for name, df in src.items():
        rows.append({
            "symbol": symbol,
            "source": name,
            "rows": int(len(df)),
            "start": str(df.index.min()) if not df.empty else "",
            "end": str(df.index.max()) if not df.empty else "",
        })
    return rows


def resample_panel(symbol: str, src: dict[str, pd.DataFrame], tf: str) -> pd.DataFrame:
    rule = TF_RULE[tf]
    k = src["kline"]
    if k.empty:
        return pd.DataFrame()
    ohlcv = k.resample(rule, origin="epoch", label="left", closed="left").agg(
        open=("open", "first"),
        high=("high", "max"),
        low=("low", "min"),
        close=("close", "last"),
        volume=("volume", "sum"),
        turnover=("turnover", "sum"),
    ).dropna(subset=["open", "high", "low", "close"])

    tf1 = src["tradeflow"]
    if not tf1.empty:
        flow = tf1.resample(rule, origin="epoch", label="left", closed="left").agg(
            buy_quote=("buy_quote", "sum"),
            sell_quote=("sell_quote", "sum"),
            buy_size=("buy_size", "sum"),
            sell_size=("sell_size", "sum"),
            trade_count=("trade_count", "sum"),
        )
        ohlcv = ohlcv.join(flow, how="left")
    for col in ["buy_quote", "sell_quote", "buy_size", "sell_size", "trade_count"]:
        if col not in ohlcv:
            ohlcv[col] = 0.0
    ohlcv[["buy_quote", "sell_quote", "buy_size", "sell_size", "trade_count"]] = ohlcv[
        ["buy_quote", "sell_quote", "buy_size", "sell_size", "trade_count"]
    ].fillna(0.0)
    quote_sum = ohlcv["buy_quote"] + ohlcv["sell_quote"]
    ohlcv["has_tradeflow"] = quote_sum > 0
    ohlcv["tbr"] = ohlcv["buy_quote"] / (quote_sum + EPS)
    ohlcv["tsr"] = ohlcv["sell_quote"] / (quote_sum + EPS)
    ohlcv["flow_imbalance"] = (ohlcv["buy_quote"] - ohlcv["sell_quote"]) / (quote_sum + EPS)

    oi = src["oi"]
    if not oi.empty:
        ohlcv = ohlcv.join(oi[["open_interest"]].resample(rule, origin="epoch", label="left", closed="left").last(), how="left")
        ohlcv["open_interest"] = ohlcv["open_interest"].ffill()
    else:
        ohlcv["open_interest"] = np.nan

    funding = src["funding"]
    if not funding.empty:
        f = funding[["funding_rate"]].sort_index()
        ohlcv = pd.merge_asof(ohlcv.sort_index(), f.sort_index(), left_index=True, right_index=True, direction="backward")
        ohlcv["funding_rate"] = ohlcv["funding_rate"].ffill()
    else:
        ohlcv["funding_rate"] = np.nan

    # mark-index premium if available.
    mark = src["mark"]
    index = src["index"]
    if not mark.empty and not index.empty:
        m = mark[["close"]].rename(columns={"close": "mark_close"}).resample(rule, origin="epoch", label="left", closed="left").last()
        ix = index[["close"]].rename(columns={"close": "index_close"}).resample(rule, origin="epoch", label="left", closed="left").last()
        ohlcv = ohlcv.join([m, ix], how="left")
        ohlcv[["mark_close", "index_close"]] = ohlcv[["mark_close", "index_close"]].ffill()
        ohlcv["mark_index_premium"] = (ohlcv["mark_close"] - ohlcv["index_close"]) / (ohlcv["index_close"] + EPS)
    else:
        ohlcv["mark_index_premium"] = np.nan

    ohlcv["symbol"] = symbol
    ohlcv["timeframe"] = tf
    return build_features(ohlcv)


def rsi_wilder(close: pd.Series, length: int) -> pd.Series:
    delta = close.diff()
    gain = delta.where(delta > 0, 0.0)
    loss = (-delta).where(delta < 0, 0.0)
    avg_gain = gain.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    avg_loss = loss.ewm(alpha=1 / length, adjust=False, min_periods=length).mean()
    rs = avg_gain / (avg_loss + EPS)
    return 100 - 100 / (1 + rs)


def rolling_z(s: pd.Series, window: int = 100) -> pd.Series:
    med = s.rolling(window, min_periods=max(20, window // 2)).median()
    mad = (s - med).abs().rolling(window, min_periods=max(20, window // 2)).median()
    return (s - med) / (1.4826 * mad.replace(0.0, np.nan) + EPS)


def build_features(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    out["log_ret"] = np.log(out["close"] / out["close"].shift(1))
    for n in [7, 14, 21]:
        out[f"rsi{n}"] = rsi_wilder(out["close"], n)
    lower = (out["close"] < out["close"].shift(1)).astype(int)
    upper = (out["close"] > out["close"].shift(1)).astype(int)
    for w in [2, 3, 4, 5]:
        out[f"clc{w}"] = lower.rolling(w, min_periods=w).sum()
        out[f"cuc{w}"] = upper.rolling(w, min_periods=w).sum()
    out["vol_sma20"] = out["volume"].rolling(20, min_periods=10).mean()
    out["vol_spike"] = out["volume"] / (out["vol_sma20"] + EPS)
    out["tbr_p100"] = out["tbr"].rolling(100, min_periods=50).rank(pct=True)
    out["tsr_p100"] = out["tsr"].rolling(100, min_periods=50).rank(pct=True)
    out["trade_count_p100"] = out["trade_count"].rolling(100, min_periods=50).rank(pct=True)
    out["flow_z"] = rolling_z(out["flow_imbalance"], 100).fillna(0.0)
    if out["open_interest"].notna().sum() > 100:
        d_oi = np.log(out["open_interest"] / out["open_interest"].shift(1))
        out["doi_z"] = rolling_z(d_oi, 100).fillna(0.0)
    else:
        out["doi_z"] = 0.0
    tr = pd.concat([
        out["high"] - out["low"],
        (out["high"] - out["close"].shift(1)).abs(),
        (out["low"] - out["close"].shift(1)).abs(),
    ], axis=1).max(axis=1)
    out["atr_pct"] = tr.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / (out["close"] + EPS)
    out["ema96"] = out["close"].ewm(span=96, adjust=False, min_periods=48).mean()
    out["ema288"] = out["close"].ewm(span=288, adjust=False, min_periods=144).mean()
    out["trend96"] = np.where(out["close"] >= out["ema96"], "above_ema96", "below_ema96")
    out["trend288"] = np.where(out["close"] >= out["ema288"], "above_ema288", "below_ema288")
    out["hour"] = out.index.hour
    out["session"] = out["hour"] // 4
    out["atr_q"] = pd.qcut(out["atr_pct"].rank(method="first"), 5, labels=False, duplicates="drop")
    out["premium_z"] = rolling_z(out["mark_index_premium"].fillna(0.0), 100).fillna(0.0)
    return out.dropna(subset=["rsi7", "atr_pct"])


def session_mask(df: pd.DataFrame, sess: object) -> pd.Series:
    if sess == "ALL":
        return pd.Series(True, index=df.index)
    if isinstance(sess, str) and sess.startswith("H"):
        return df["hour"] == int(sess[1:])
    return df["session"] == int(sess)


def make_candidates(symbol: str, tf: str, df: pd.DataFrame) -> list[Candidate]:
    cands: list[Candidate] = []
    sessions: list[object] = ["ALL", 0, 1, 2, 3, 4, 5, "H05"]
    long_filters = {
        "none": pd.Series(True, index=df.index),
        "tsr70": df["tsr_p100"] >= 0.70,
        "tsr80": df["tsr_p100"] >= 0.80,
        "flowNeg": df["flow_z"] <= 0.0,
        "flowNeg1": df["flow_z"] <= -1.0,
        "oiDrop": df["doi_z"] <= -0.5,
        "vol15": df["vol_spike"] >= 1.5,
        "belowE96": df["trend96"] == "below_ema96",
        "atrNotTop": df["atr_q"].fillna(2) <= 3,
    }
    short_filters = {
        "none": pd.Series(True, index=df.index),
        "tbr70": df["tbr_p100"] >= 0.70,
        "tbr80": df["tbr_p100"] >= 0.80,
        "flowPos": df["flow_z"] >= 0.0,
        "flowPos1": df["flow_z"] >= 1.0,
        "oiRise": df["doi_z"] >= 0.5,
        "vol15": df["vol_spike"] >= 1.5,
        "aboveE96": df["trend96"] == "above_ema96",
        "atrNotTop": df["atr_q"].fillna(2) <= 3,
    }
    for rsi_col in ["rsi7", "rsi14"]:
        for rsi_t in [20, 25, 30, 35]:
            for sess in sessions:
                sm = session_mask(df, sess)
                for fname, fm in long_filters.items():
                    mask = (df[rsi_col] <= rsi_t) & sm & fm
                    name = f"LONG_{rsi_col}<={rsi_t}_S{sess}_{fname}"
                    cands.append(Candidate(symbol, tf, 1, name, mask))
        for rsi_t in [65, 70, 75, 80]:
            for sess in sessions:
                sm = session_mask(df, sess)
                for fname, fm in short_filters.items():
                    mask = (df[rsi_col] >= rsi_t) & sm & fm
                    name = f"SHORT_{rsi_col}>={rsi_t}_S{sess}_{fname}"
                    cands.append(Candidate(symbol, tf, -1, name, mask))
    for w in [3, 4, 5]:
        for sess in sessions:
            sm = session_mask(df, sess)
            cands.append(Candidate(symbol, tf, 1, f"LONG_CLC{w}_S{sess}_tsr80", (df[f"clc{w}"] == w) & sm & (df["tsr_p100"] >= 0.80)))
            cands.append(Candidate(symbol, tf, 1, f"LONG_CLC{w}_S{sess}_flowNeg", (df[f"clc{w}"] == w) & sm & (df["flow_z"] <= -0.5)))
            cands.append(Candidate(symbol, tf, -1, f"SHORT_CUC{w}_S{sess}_tbr80", (df[f"cuc{w}"] == w) & sm & (df["tbr_p100"] >= 0.80)))
    return cands


def quick_events(df: pd.DataFrame, cand: Candidate) -> pd.DataFrame:
    idxs = np.flatnonzero(cand.mask.fillna(False).to_numpy(bool))
    close = df["close"].to_numpy(float)
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    risk_arr = np.maximum(df["atr_pct"].to_numpy(float), 0.003)
    max_h = max(HORIZONS[cand.timeframe])
    rows = []
    n_bars = len(df)
    for pos in idxs:
        entry = pos + 1
        if entry + max_h >= n_bars:
            continue
        entry_price = close[entry]
        risk = risk_arr[entry] if np.isfinite(risk_arr[entry]) else 0.003
        row = {
            "symbol": cand.symbol,
            "timeframe": cand.timeframe,
            "candidate": cand.name,
            "direction": cand.direction,
            "signal_ts": df.index[pos],
            "entry_ts": df.index[entry],
            "entry_pos": entry,
            "entry_price": entry_price,
            "risk_pct": risk,
            "split": split_label_from_pos(entry, n_bars),
            "hour": int(df["hour"].iloc[pos]),
            "session": int(df["session"].iloc[pos]),
            "atr_q": int(df["atr_q"].iloc[pos]) if pd.notna(df["atr_q"].iloc[pos]) else -1,
            "trend96": df["trend96"].iloc[pos],
            "trend288": df["trend288"].iloc[pos],
            "flow_z": float(df["flow_z"].iloc[pos]),
            "doi_z": float(df["doi_z"].iloc[pos]),
            "tbr_p100": float(df["tbr_p100"].iloc[pos]) if pd.notna(df["tbr_p100"].iloc[pos]) else np.nan,
            "tsr_p100": float(df["tsr_p100"].iloc[pos]) if pd.notna(df["tsr_p100"].iloc[pos]) else np.nan,
            "vol_spike": float(df["vol_spike"].iloc[pos]) if pd.notna(df["vol_spike"].iloc[pos]) else np.nan,
            "has_tradeflow": bool(df["has_tradeflow"].iloc[pos]),
        }
        for h in HORIZONS[cand.timeframe]:
            gross = cand.direction * (close[entry + h] / entry_price - 1.0)
            net = gross - COST_RT
            row[f"net_{h}"] = net
            row[f"r_{h}"] = net / risk
        if cand.direction > 0:
            mfe = np.nanmax(high[entry:entry + max_h + 1] / entry_price - 1.0)
            mae = np.nanmin(low[entry:entry + max_h + 1] / entry_price - 1.0)
        else:
            mfe = np.nanmax(1.0 - low[entry:entry + max_h + 1] / entry_price)
            mae = np.nanmin(1.0 - high[entry:entry + max_h + 1] / entry_price)
        row["mfe_r"] = mfe / risk
        row["mae_r"] = mae / risk
        rows.append(row)
    return pd.DataFrame(rows)


def stats_from_events(ev: pd.DataFrame, horizon: int, label: str) -> dict[str, object]:
    if ev.empty:
        return {"split": label, "n": 0}
    rcol = f"r_{horizon}"
    n = len(ev)
    wins = int((ev[rcol] > 0).sum())
    return {
        "split": label,
        "n": n,
        "wr": wins / n,
        "wilson95": wilson_lower(wins, n),
        "mean_r": float(ev[rcol].mean()),
        "median_r": float(ev[rcol].median()),
        "p10_r": float(ev[rcol].quantile(0.10)),
        "p90_r": float(ev[rcol].quantile(0.90)),
        "mfe_r_med": float(ev["mfe_r"].median()),
        "mae_r_med": float(ev["mae_r"].median()),
        "tradeflow_coverage": float(ev["has_tradeflow"].mean()) if "has_tradeflow" in ev else math.nan,
    }


def summarize_candidate(ev: pd.DataFrame, horizon: int) -> dict[str, object]:
    base = {"horizon": horizon}
    if ev.empty:
        return base | {"all_n": 0}
    first = ev.iloc[0]
    base.update({
        "symbol": first["symbol"],
        "timeframe": first["timeframe"],
        "candidate": first["candidate"],
        "direction": int(first["direction"]),
    })
    for split in ["train", "validation", "holdout", "all"]:
        part = ev if split == "all" else ev[ev["split"] == split]
        s = stats_from_events(part, horizon, split)
        for k, v in s.items():
            if k != "split":
                base[f"{split}_{k}"] = v
    return base


def quick_scan(panels: dict[tuple[str, str], pd.DataFrame], max_candidates: int) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows = []
    event_cache: dict[str, pd.DataFrame] = {}
    scanned = 0
    for (symbol, tf), df in panels.items():
        cands = make_candidates(symbol, tf, df)
        print(f"scan {symbol} {tf}: bars={len(df)} candidates={len(cands)}", flush=True)
        for cand in cands:
            ev = quick_events(df, cand)
            if len(ev) < 20:
                continue
            key = f"{symbol}|{tf}|{cand.direction}|{cand.name}"
            event_cache[key] = ev
            for horizon in HORIZONS[tf]:
                rows.append(summarize_candidate(ev, horizon))
            scanned += 1
            if scanned % 250 == 0:
                print(f"quick scanned {scanned} eventful candidates", flush=True)
    res = pd.DataFrame(rows)
    if res.empty:
        return res, event_cache
    # Rank by validation+holdout expectancy with small-n penalty.
    res["rank_score"] = (
        res.get("validation_mean_r", 0).fillna(-999) * 0.40
        + res.get("holdout_mean_r", 0).fillna(-999) * 0.45
        + res.get("all_mean_r", 0).fillna(-999) * 0.15
    )
    res["min_oos_n"] = res[["validation_n", "holdout_n"]].min(axis=1)
    res = res.sort_values(["rank_score", "min_oos_n", "holdout_wr"], ascending=[False, False, False])
    return res.head(max_candidates * 20), event_cache


def simulate_path(df: pd.DataFrame, ev: pd.DataFrame, tp_r: float, sl_r: float, max_hold: int) -> pd.DataFrame:
    if ev.empty:
        return pd.DataFrame()
    high = df["high"].to_numpy(float)
    low = df["low"].to_numpy(float)
    close = df["close"].to_numpy(float)
    rows = []
    for _, e in ev.iterrows():
        entry = int(e["entry_pos"])
        if entry + max_hold >= len(df):
            continue
        direction = int(e["direction"])
        entry_price = float(e["entry_price"])
        risk = float(e["risk_pct"])
        if direction > 0:
            tp = entry_price * (1.0 + tp_r * risk)
            sl = entry_price * (1.0 - sl_r * risk)
        else:
            tp = entry_price * (1.0 - tp_r * risk)
            sl = entry_price * (1.0 + sl_r * risk)
        outcome_r: float | None = None
        exit_pos = entry + max_hold
        exit_reason = "timeout"
        for pos in range(entry, entry + max_hold + 1):
            if direction > 0:
                hit_tp = high[pos] >= tp
                hit_sl = low[pos] <= sl
            else:
                hit_tp = low[pos] <= tp
                hit_sl = high[pos] >= sl
            if hit_tp and hit_sl:
                outcome_r = -sl_r  # conservative same-bar ambiguity
                exit_pos = pos
                exit_reason = "same_bar_sl"
                break
            if hit_sl:
                outcome_r = -sl_r
                exit_pos = pos
                exit_reason = "sl"
                break
            if hit_tp:
                outcome_r = tp_r
                exit_pos = pos
                exit_reason = "tp"
                break
        if outcome_r is None:
            gross = direction * (close[exit_pos] / entry_price - 1.0)
            outcome_r = (gross - COST_RT) / risk
        row = e.to_dict()
        row.update({
            "tp_r": tp_r,
            "sl_r": sl_r,
            "max_hold": max_hold,
            "exit_ts": df.index[exit_pos],
            "exit_reason": exit_reason,
            "outcome_r": outcome_r,
            "win": outcome_r > 0,
        })
        rows.append(row)
    return pd.DataFrame(rows)


def equity_stats(trades: pd.DataFrame) -> dict[str, object]:
    if trades.empty:
        return {"n": 0}
    trades = trades.sort_values("entry_ts")
    r = trades["outcome_r"].to_numpy(float)
    eq = np.cumsum(r)
    peak = np.maximum.accumulate(eq)
    dd = eq - peak
    wins = r[r > 0]
    losses = r[r <= 0]
    gross_win = float(wins.sum()) if len(wins) else 0.0
    gross_loss = float(-losses.sum()) if len(losses) else 0.0
    n = len(r)
    win_count = int((r > 0).sum())
    return {
        "n": n,
        "wr": win_count / n,
        "wilson95": wilson_lower(win_count, n),
        "mean_r": float(np.mean(r)),
        "median_r": float(np.median(r)),
        "profit_factor": gross_win / gross_loss if gross_loss else math.inf,
        "max_dd_r": float(dd.min()) if len(dd) else 0.0,
        "p10_r": float(np.quantile(r, 0.10)),
        "p05_r": float(np.quantile(r, 0.05)),
        "avg_win_r": float(np.mean(wins)) if len(wins) else 0.0,
        "avg_loss_r": float(np.mean(losses)) if len(losses) else 0.0,
        "tp_rate": float((trades["exit_reason"] == "tp").mean()),
        "sl_rate": float((trades["exit_reason"].isin(["sl", "same_bar_sl"])).mean()),
    }


def path_grid(top_quick: pd.DataFrame, event_cache: dict[str, pd.DataFrame], panels: dict[tuple[str, str], pd.DataFrame], max_base: int) -> tuple[pd.DataFrame, dict[str, pd.DataFrame]]:
    rows = []
    trade_cache: dict[str, pd.DataFrame] = {}
    base_rows = top_quick.drop_duplicates(["symbol", "timeframe", "direction", "candidate"]).head(max_base)
    for _, b in base_rows.iterrows():
        key = f"{b['symbol']}|{b['timeframe']}|{int(b['direction'])}|{b['candidate']}"
        ev = event_cache.get(key)
        if ev is None or ev.empty:
            continue
        df = panels[(str(b["symbol"]), str(b["timeframe"]))]
        for tp_r, sl_r, max_hold in itertools.product(TP_GRID, SL_GRID, MAX_HOLD_GRID[str(b["timeframe"])]):
            tr = simulate_path(df, ev, tp_r, sl_r, max_hold)
            if tr.empty:
                continue
            base = {
                "symbol": b["symbol"],
                "timeframe": b["timeframe"],
                "candidate": b["candidate"],
                "direction": int(b["direction"]),
                "tp_r": tp_r,
                "sl_r": sl_r,
                "max_hold": max_hold,
            }
            for split in ["train", "validation", "holdout", "all"]:
                part = tr if split == "all" else tr[tr["split"] == split]
                st = equity_stats(part)
                for k, v in st.items():
                    base[f"{split}_{k}"] = v
            rows.append(base)
            cache_key = json.dumps(base | {"kind": "path"}, sort_keys=True, default=str)
            trade_cache[cache_key] = tr
    res = pd.DataFrame(rows)
    if res.empty:
        return res, trade_cache
    res["rank_score"] = (
        res.get("validation_mean_r", 0).fillna(-999) * 0.35
        + res.get("holdout_mean_r", 0).fillna(-999) * 0.45
        + res.get("all_mean_r", 0).fillna(-999) * 0.20
        + res.get("holdout_profit_factor", 0).replace([np.inf], 10).fillna(0) * 0.03
        + res.get("holdout_max_dd_r", 0).fillna(-999) * 0.02
    )
    res = res.sort_values(["rank_score", "holdout_n", "holdout_wr"], ascending=[False, False, False])
    return res, trade_cache


def filter_variants(tr: pd.DataFrame) -> dict[str, pd.Series]:
    variants: dict[str, pd.Series] = {"none": pd.Series(True, index=tr.index)}
    if "hour" in tr:
        for h in sorted(tr["hour"].dropna().unique()):
            variants[f"hour={int(h)}"] = tr["hour"] == h
        variants["hour_not_6_7"] = ~tr["hour"].isin([6, 7])
    if "atr_q" in tr:
        variants["atr_not_top"] = tr["atr_q"].fillna(2) <= 3
        variants["atr_low_mid"] = tr["atr_q"].fillna(2) <= 2
    if "flow_z" in tr:
        variants["flow_neg"] = tr["flow_z"] < 0
        variants["flow_pos"] = tr["flow_z"] > 0
    if "doi_z" in tr:
        variants["oi_down"] = tr["doi_z"] < 0
        variants["oi_up"] = tr["doi_z"] > 0
    if "tsr_p100" in tr:
        variants["tsr_hi70"] = tr["tsr_p100"] >= 0.70
        variants["tsr_lo70"] = tr["tsr_p100"] < 0.70
    if "tbr_p100" in tr:
        variants["tbr_hi70"] = tr["tbr_p100"] >= 0.70
        variants["tbr_lo70"] = tr["tbr_p100"] < 0.70
    if "trend96" in tr:
        for val in sorted(tr["trend96"].dropna().unique()):
            variants[f"trend96={val}"] = tr["trend96"] == val
    return variants


def rescue_screen(path_res: pd.DataFrame, trade_cache: dict[str, pd.DataFrame], max_rows: int = 30) -> pd.DataFrame:
    rows = []
    top = path_res.head(max_rows)
    for _, row in top.iterrows():
        ident = {
            "symbol": row["symbol"],
            "timeframe": row["timeframe"],
            "candidate": row["candidate"],
            "direction": int(row["direction"]),
            "tp_r": float(row["tp_r"]),
            "sl_r": float(row["sl_r"]),
            "max_hold": int(row["max_hold"]),
        }
        key = json.dumps(ident | {"kind": "path"}, sort_keys=True, default=str)
        tr = trade_cache.get(key)
        if tr is None or tr.empty:
            # float/int JSON exactness fallback
            for k, v in trade_cache.items():
                if all(str(ident[x]) in k for x in ["symbol", "timeframe", "candidate"]):
                    tr = v
                    break
        if tr is None or tr.empty:
            continue
        for fname, mask in filter_variants(tr).items():
            ft = tr[mask.fillna(False)]
            if len(ft) < max(20, len(tr) * 0.20):
                continue
            out = dict(ident)
            out["filter"] = fname
            for split in ["train", "validation", "holdout", "all"]:
                part = ft if split == "all" else ft[ft["split"] == split]
                st = equity_stats(part)
                for k2, v2 in st.items():
                    out[f"{split}_{k2}"] = v2
            rows.append(out)
    res = pd.DataFrame(rows)
    if res.empty:
        return res
    res["rank_score"] = (
        res.get("holdout_mean_r", 0).fillna(-999) * 0.45
        + res.get("validation_mean_r", 0).fillna(-999) * 0.35
        + res.get("holdout_profit_factor", 0).replace([np.inf], 10).fillna(0) * 0.04
        + res.get("holdout_max_dd_r", 0).fillna(-999) * 0.03
    )
    return res.sort_values(["rank_score", "holdout_n", "holdout_wr"], ascending=[False, False, False])


def loss_cause_breakdown(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return pd.DataFrame()
    rows = []
    loss = trades[trades["outcome_r"] < 0].copy()
    if loss.empty:
        return pd.DataFrame()
    loss["flow_bucket"] = np.where(loss["flow_z"] < 0, "flow_neg", "flow_pos")
    loss["oi_bucket"] = np.where(loss["doi_z"] < 0, "oi_down", "oi_up")
    loss["tsr_bucket"] = np.where(loss["tsr_p100"] >= 0.70, "tsr_hi70", "tsr_lo70")
    loss["tbr_bucket"] = np.where(loss["tbr_p100"] >= 0.70, "tbr_hi70", "tbr_lo70")
    for col in ["symbol", "timeframe", "hour", "session", "atr_q", "trend96", "flow_bucket", "oi_bucket", "tsr_bucket", "tbr_bucket", "exit_reason"]:
        for key, g in loss.groupby(col, dropna=False):
            rows.append({
                "dimension": col,
                "value": key,
                "loss_n": len(g),
                "loss_r_sum": float(g["outcome_r"].sum()),
                "loss_r_mean": float(g["outcome_r"].mean()),
                "loss_share": len(g) / len(loss),
            })
    return pd.DataFrame(rows).sort_values(["loss_r_sum", "loss_n"], ascending=[True, False])


def write_report(coverage: pd.DataFrame, quick: pd.DataFrame, path_res: pd.DataFrame, rescue: pd.DataFrame, causes: pd.DataFrame) -> None:
    strict = pd.DataFrame()
    if not path_res.empty:
        strict = path_res[
            (path_res["holdout_n"] >= 500)
            & (path_res["holdout_wr"] >= 0.60)
            & (path_res["holdout_mean_r"] > 0)
            & (path_res["validation_mean_r"] > 0)
            & (path_res["holdout_profit_factor"] > 1.10)
        ]
    lines = [
        "# LINK/AVAX Multi-Timeframe Optimizer",
        "",
        "Symbols: `AVAXUSDT`, `LINKUSDT` on Bybit Linear cached data.",
        "",
        "## Coverage",
        "",
        df_to_md(coverage, 40),
        "",
        "## Strict deployable candidates",
        "",
        df_to_md(strict.head(30)) if not strict.empty else "_none_",
        "",
        "## Top close-to-close scan rows",
        "",
        df_to_md(quick.head(30)) if not quick.empty else "_none_",
        "",
        "## Top conservative TP/SL path rows",
        "",
        df_to_md(path_res.head(30)) if not path_res.empty else "_none_",
        "",
        "## Rescue-filter rows",
        "",
        df_to_md(rescue.head(30)) if not rescue.empty else "_none_",
        "",
        "## Loss cause breakdown",
        "",
        df_to_md(causes.head(40)) if not causes.empty else "_none_",
        "",
        "## Verdict notes",
        "",
        "- Strict gate remains: holdout n>=500, WR>=60%, positive validation+holdout expectancy, PF>1.10.",
        "- Rows below gate are research pockets only; do not deploy from scanner WR alone.",
    ]
    (ART / "MULTITF_OPTIMIZER_REPORT.md").write_text("\n".join(lines), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", nargs="+", default=["AVAXUSDT", "LINKUSDT"])
    ap.add_argument("--timeframes", nargs="+", default=["5m", "15m", "1h", "4h"])
    ap.add_argument("--max-base", type=int, default=80)
    ap.add_argument("--quick-max", type=int, default=120)
    args = ap.parse_args()

    coverage_rows = []
    panels: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol in args.symbols:
        src = load_1m_sources(symbol)
        coverage_rows.extend(coverage_summary(symbol, src))
        for tf in args.timeframes:
            panel = resample_panel(symbol, src, tf)
            if panel.empty:
                print(f"skip {symbol} {tf}: no panel", flush=True)
                continue
            panels[(symbol, tf)] = panel
            print(f"panel {symbol} {tf}: {len(panel)} bars {panel.index.min()} -> {panel.index.max()}", flush=True)
    coverage = pd.DataFrame(coverage_rows)
    coverage.to_csv(ART / "coverage.csv", index=False)
    if not panels:
        print("ERROR no panels", flush=True)
        return 2

    quick, event_cache = quick_scan(panels, args.quick_max)
    quick.to_csv(ART / "quick_scan_results.csv", index=False)
    if quick.empty:
        write_report(coverage, quick, pd.DataFrame(), pd.DataFrame(), pd.DataFrame())
        print("NO QUICK RESULTS", flush=True)
        return 0

    path_res, trade_cache = path_grid(quick, event_cache, panels, args.max_base)
    path_res.to_csv(ART / "path_grid_results.csv", index=False)
    rescue = rescue_screen(path_res, trade_cache, 30)
    rescue.to_csv(ART / "rescue_filter_results.csv", index=False)

    # Cause breakdown from best path row trades.
    causes = pd.DataFrame()
    if not path_res.empty and trade_cache:
        best = path_res.iloc[0]
        ident = {
            "symbol": best["symbol"],
            "timeframe": best["timeframe"],
            "candidate": best["candidate"],
            "direction": int(best["direction"]),
            "tp_r": float(best["tp_r"]),
            "sl_r": float(best["sl_r"]),
            "max_hold": int(best["max_hold"]),
        }
        key = json.dumps(ident | {"kind": "path"}, sort_keys=True, default=str)
        tr = trade_cache.get(key)
        if tr is not None:
            tr.to_csv(ART / "best_candidate_trades.csv", index=False)
            causes = loss_cause_breakdown(tr)
            causes.to_csv(ART / "loss_cause_breakdown.csv", index=False)

    write_report(coverage, quick, path_res, rescue, causes)
    print(f"REPORT {ART / 'MULTITF_OPTIMIZER_REPORT.md'}", flush=True)
    if not path_res.empty:
        print(path_res.head(20).to_string(index=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
