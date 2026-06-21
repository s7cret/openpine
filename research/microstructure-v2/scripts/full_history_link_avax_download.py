#!/usr/bin/env python3
"""Resumable full-history Bybit Linear downloader for AVAX/LINK research.

Downloads finalized 1m OHLCV/mark/index/premium, 5m OI, funding, and daily
tradeflow aggregates. Raw public trade archives are parsed through /tmp and not
kept, to avoid filling disk.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
import gzip
import json
import os
import shutil
import subprocess
import tempfile
import time

import numpy as np
import pandas as pd

ROOT = Path("/home/moltbot1/.openclaw/workspace/openpine/research/microstructure-v2")
CACHE = ROOT / "data" / "cache"
ART = ROOT / "artifacts" / "full_history_link_avax"
ART.mkdir(parents=True, exist_ok=True)
STATUS_PATH = ART / "DOWNLOAD_STATUS.json"
UA = "OpenPineFullHistory/0.1"
BYBIT_API = "https://api.bybit.com"
BYBIT_ARCHIVE = "https://public.bybit.com/trading"
SYMBOLS = ["AVAXUSDT", "LINKUSDT"]
# Bybit launchTime for Linear instruments; LINK's API launchTime is 2018-01-01,
# but older API/archive ranges may simply return empty/404 and are skipped.
LAUNCH_START = {
    "AVAXUSDT": datetime(2021, 9, 15, tzinfo=timezone.utc),
    "LINKUSDT": datetime(2018, 1, 1, tzinfo=timezone.utc),
}
END = datetime.now(timezone.utc).replace(second=0, microsecond=0)
TODAY_UTC = datetime(END.year, END.month, END.day, tzinfo=timezone.utc)
TRADEFLOW_END = min(END, TODAY_UTC)  # public daily archive is closed-days only


def utc_ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


def save_status(**extra: object) -> None:
    current = {}
    if STATUS_PATH.exists():
        try:
            current = json.loads(STATUS_PATH.read_text())
        except Exception:
            current = {}
    current.update(extra)
    current["updated_at"] = datetime.now(timezone.utc).isoformat()
    STATUS_PATH.write_text(json.dumps(current, indent=2, sort_keys=True), encoding="utf-8")


def http_get_json(endpoint: str, params: dict[str, object], retries: int = 8) -> dict:
    url = endpoint + "?" + urlencode(params)
    last: Exception | None = None
    for attempt in range(retries):
        try:
            cp = subprocess.run(
                ["curl", "-fsS", "--connect-timeout", "10", "--max-time", "35", "-A", UA, url],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            data = json.loads(cp.stdout)
            if data.get("retCode") == 10006:  # Bybit rate limit
                time.sleep(min(30.0, 3.0 + attempt * 3.0))
                continue
            return data
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(min(20.0, 0.8 * (attempt + 1)))
    raise RuntimeError(f"GET failed {url}: {last}")


def month_ranges(start: datetime, end: datetime):
    cur = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    if cur < start:
        cur = start
    while cur < end:
        if cur.month == 12:
            nxt = datetime(cur.year + 1, 1, 1, tzinfo=timezone.utc)
        else:
            nxt = datetime(cur.year, cur.month + 1, 1, tzinfo=timezone.utc)
        yield cur, min(nxt, end)
        cur = nxt


def year_ranges(start: datetime, end: datetime):
    cur = datetime(start.year, 1, 1, tzinfo=timezone.utc)
    if cur < start:
        cur = start
    while cur < end:
        nxt = datetime(cur.year + 1, 1, 1, tzinfo=timezone.utc)
        yield cur, min(nxt, end)
        cur = nxt


def iter_days(start: datetime, end: datetime):
    cur = start.date()
    last = (end - timedelta(microseconds=1)).date()
    while cur <= last:
        yield cur
        cur += timedelta(days=1)


def bybit_list_to_ohlc(rows: list, kind: str) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame()
    cols = ["time", "open", "high", "low", "close", "volume", "turnover"] if kind == "kline" else ["time", "open", "high", "low", "close"]
    trimmed = [r[: len(cols)] for r in rows if len(r) >= len(cols)]
    if not trimmed:
        return pd.DataFrame()
    df = pd.DataFrame(trimmed, columns=cols)
    df["timestamp"] = pd.to_datetime(pd.to_numeric(df["time"], errors="coerce"), unit="ms", utc=True)
    for c in cols[1:]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.drop(columns=["time"]).drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp")


def fetch_ohlc_chunk(symbol: str, feature: str, start: datetime, end: datetime) -> pd.DataFrame:
    endpoint_map = {
        "kline": (f"{BYBIT_API}/v5/market/kline", "kline"),
        "mark": (f"{BYBIT_API}/v5/market/mark-price-kline", "price"),
        "index": (f"{BYBIT_API}/v5/market/index-price-kline", "price"),
        "premium": (f"{BYBIT_API}/v5/market/premium-index-price-kline", "price"),
    }
    endpoint, kind = endpoint_map[feature]
    frames: list[pd.DataFrame] = []
    cur_ms = utc_ms(start)
    end_ms = utc_ms(end)
    step_ms = 999 * 60_000
    while cur_ms < end_ms:
        chunk_end = min(end_ms, cur_ms + step_ms)
        data = http_get_json(endpoint, {"category": "linear", "symbol": symbol, "interval": "1", "start": cur_ms, "end": chunk_end, "limit": 1000})
        if data.get("retCode") != 0:
            print(f"ERR {symbol}/{feature}: {data.get('retMsg')}", flush=True)
            time.sleep(8)
            continue
        frame = bybit_list_to_ohlc(((data.get("result") or {}).get("list") or []), kind)
        if not frame.empty:
            frames.append(frame)
        cur_ms = chunk_end + 60_000
        time.sleep(0.08)
    if not frames:
        out = pd.DataFrame()
        out.index.name = "timestamp"
        return out
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out = out[(out.index >= pd.Timestamp(start)) & (out.index < pd.Timestamp(end))]
    out.index.name = "timestamp"
    return out


def fetch_oi_chunk(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    cur_ms = utc_ms(start)
    end_ms = utc_ms(end)
    step_ms = 199 * 5 * 60_000
    while cur_ms < end_ms:
        chunk_end = min(end_ms, cur_ms + step_ms)
        data = http_get_json(
            f"{BYBIT_API}/v5/market/open-interest",
            {"category": "linear", "symbol": symbol, "intervalTime": "5min", "startTime": cur_ms, "endTime": chunk_end, "limit": 200},
        )
        if data.get("retCode") != 0:
            print(f"ERR {symbol}/OI: {data.get('retMsg')}", flush=True)
            time.sleep(8)
            continue
        rows = ((data.get("result") or {}).get("list") or [])
        if rows:
            df = pd.DataFrame(rows)
            df["timestamp"] = pd.to_datetime(pd.to_numeric(df["timestamp"], errors="coerce"), unit="ms", utc=True)
            df["open_interest"] = pd.to_numeric(df["openInterest"], errors="coerce")
            frames.append(df[["timestamp", "open_interest"]].set_index("timestamp"))
        cur_ms = chunk_end + 5 * 60_000
        time.sleep(0.08)
    if not frames:
        out = pd.DataFrame(columns=["open_interest"])
        out.index.name = "timestamp"
        return out
    out = pd.concat(frames).sort_index()
    out = out[~out.index.duplicated(keep="last")]
    out = out[(out.index >= pd.Timestamp(start)) & (out.index < pd.Timestamp(end))]
    out.index.name = "timestamp"
    return out


def fetch_funding_chunk(symbol: str, start: datetime, end: datetime) -> pd.DataFrame:
    # Funding interval is 8h; monthly window fits Bybit limit=200 safely.
    data = http_get_json(
        f"{BYBIT_API}/v5/market/funding/history",
        {"category": "linear", "symbol": symbol, "startTime": utc_ms(start - timedelta(hours=8)), "endTime": utc_ms(end + timedelta(hours=8)), "limit": 200},
    )
    rows = ((data.get("result") or {}).get("list") or []) if data.get("retCode") == 0 else []
    if not rows:
        out = pd.DataFrame(columns=["funding_rate"])
        out.index.name = "timestamp"
        return out
    df = pd.DataFrame(rows)
    df["timestamp"] = pd.to_datetime(pd.to_numeric(df["fundingRateTimestamp"], errors="coerce"), unit="ms", utc=True)
    df["funding_rate"] = pd.to_numeric(df["fundingRate"], errors="coerce")
    out = df[["timestamp", "funding_rate"]].drop_duplicates("timestamp").sort_values("timestamp").set_index("timestamp")
    out.index.name = "timestamp"
    return out


def write_parquet_atomic(df: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent))
    os.close(fd)
    tmp = Path(tmp_name)
    try:
        df.to_parquet(tmp)
        tmp.replace(path)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def download_ohlc_oi_funding() -> None:
    for symbol in SYMBOLS:
        start = LAUNCH_START[symbol]
        for feature in ["kline", "mark", "index", "premium"]:
            for a, b in month_ranges(start, END):
                out = CACHE / symbol / f"{feature}_{a:%Y%m%d}_{b:%Y%m%d}.parquet"
                if out.exists():
                    continue
                df = fetch_ohlc_chunk(symbol, feature, a, b)
                write_parquet_atomic(df, out)
                print(f"{symbol}/{feature} {a:%Y-%m-%d}->{b:%Y-%m-%d}: {len(df)}", flush=True)
                save_status(symbol=symbol, phase=f"{feature}", chunk=f"{a:%Y%m%d}_{b:%Y%m%d}", rows=int(len(df)))
        for a, b in month_ranges(start, END):
            out = CACHE / symbol / f"oi5m_{a:%Y%m%d}_{b:%Y%m%d}.parquet"
            if not out.exists():
                df = fetch_oi_chunk(symbol, a, b)
                write_parquet_atomic(df, out)
                print(f"{symbol}/oi5m {a:%Y-%m-%d}->{b:%Y-%m-%d}: {len(df)}", flush=True)
                save_status(symbol=symbol, phase="oi5m", chunk=f"{a:%Y%m%d}_{b:%Y%m%d}", rows=int(len(df)))
            out_f = CACHE / symbol / f"funding_{a:%Y%m%d}_{b:%Y%m%d}.parquet"
            if not out_f.exists():
                df = fetch_funding_chunk(symbol, a, b)
                write_parquet_atomic(df, out_f)
                print(f"{symbol}/funding {a:%Y-%m-%d}->{b:%Y-%m-%d}: {len(df)}", flush=True)
                save_status(symbol=symbol, phase="funding", chunk=f"{a:%Y%m%d}_{b:%Y%m%d}", rows=int(len(df)))


def download_to_tmp(url: str, tmp_path: Path, retries: int = 4) -> bool:
    for attempt in range(retries):
        try:
            cp = subprocess.run(
                ["curl", "-fL", "--connect-timeout", "10", "--max-time", "120", "-A", UA, "-o", str(tmp_path), url],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if cp.returncode == 0:
                return True
            if "404" in cp.stderr or "The requested URL returned error: 404" in cp.stderr:
                return False
            time.sleep(min(25.0, 2.0 * (attempt + 1)))
        except Exception as exc:  # noqa: BLE001
            print(f"WARN download {url}: {exc}", flush=True)
            time.sleep(min(25.0, 2.0 * (attempt + 1)))
    return False


def parse_tradeflow_day(symbol: str, d: date) -> tuple[bool, int]:
    daily_cache = CACHE / symbol / "daily_tradeflow_1m" / f"{symbol}_{d:%Y%m%d}.parquet"
    if daily_cache.exists():
        return True, 1440
    day_start = pd.Timestamp(datetime(d.year, d.month, d.day, tzinfo=timezone.utc))
    day_end = day_start + pd.Timedelta(days=1)
    url = f"{BYBIT_ARCHIVE}/{symbol}/{symbol}{d:%Y-%m-%d}.csv.gz"
    with tempfile.NamedTemporaryFile(prefix=f"{symbol}_{d:%Y%m%d}_", suffix=".csv.gz", delete=False) as tmp_fh:
        tmp = Path(tmp_fh.name)
    ok = download_to_tmp(url, tmp)
    if not ok:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass
        return False, 0
    try:
        try:
            df = pd.read_csv(tmp, compression="gzip", usecols=["timestamp", "side", "size", "price"], dtype={"side": "category"})
        except Exception:
            # Some old archives have uppercase headers or slightly different order.
            df = pd.read_csv(tmp, compression="gzip")
            df.columns = [str(c).strip().lower() for c in df.columns]
            df = df[["timestamp", "side", "size", "price"]]
        if df.empty:
            return False, 0
        ts_num = pd.to_numeric(df["timestamp"], errors="coerce")
        # Bybit public archive is seconds float in observed files; tolerate ms.
        unit = "ms" if ts_num.dropna().median() > 10_000_000_000 else "s"
        df["timestamp"] = pd.to_datetime(ts_num, unit=unit, utc=True)
        df = df[(df["timestamp"] >= day_start) & (df["timestamp"] < day_end)]
        if df.empty:
            return False, 0
        df["size"] = pd.to_numeric(df["size"], errors="coerce")
        df["price"] = pd.to_numeric(df["price"], errors="coerce")
        df["quote"] = df["size"] * df["price"]
        side_lower = df["side"].astype(str).str.lower()
        df["buy_quote"] = np.where(side_lower == "buy", df["quote"], 0.0)
        df["sell_quote"] = np.where(side_lower == "sell", df["quote"], 0.0)
        df["buy_size"] = np.where(side_lower == "buy", df["size"], 0.0)
        df["sell_size"] = np.where(side_lower == "sell", df["size"], 0.0)
        df = df.set_index("timestamp").sort_index()
        agg = df.resample("1min", origin="epoch", label="left", closed="left").agg(
            buy_quote=("buy_quote", "sum"),
            sell_quote=("sell_quote", "sum"),
            buy_size=("buy_size", "sum"),
            sell_size=("sell_size", "sum"),
            trade_count=("price", "count"),
        )
        full_idx = pd.date_range(start=day_start, end=day_end - pd.Timedelta(minutes=1), freq="1min", tz="UTC")
        agg = agg.reindex(full_idx).fillna(0.0)
        agg.index.name = "timestamp"
        write_parquet_atomic(agg, daily_cache)
        return True, int(len(df))
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def assemble_tradeflow_chunks(symbol: str, start: datetime, end: datetime) -> None:
    daily_dir = CACHE / symbol / "daily_tradeflow_1m"
    for a, b in year_ranges(start, end):
        out = CACHE / symbol / f"tradeflow_1m_{a:%Y%m%d}_{b:%Y%m%d}.parquet"
        if out.exists():
            continue
        frames: list[pd.DataFrame] = []
        available = 0
        total = 0
        for d in iter_days(a, b):
            total += 1
            p = daily_dir / f"{symbol}_{d:%Y%m%d}.parquet"
            if p.exists():
                frames.append(pd.read_parquet(p))
                available += 1
        if frames:
            df = pd.concat(frames).sort_index()
            df = df[~df.index.duplicated(keep="last")]
        else:
            df = pd.DataFrame(columns=["buy_quote", "sell_quote", "buy_size", "sell_size", "trade_count"])
            df.index.name = "timestamp"
        write_parquet_atomic(df, out)
        print(f"{symbol}/tradeflow chunk {a:%Y-%m-%d}->{b:%Y-%m-%d}: days={available}/{total} rows={len(df)}", flush=True)


def download_tradeflow() -> None:
    for symbol in SYMBOLS:
        start = LAUNCH_START[symbol]
        ok_days = 0
        seen_days = 0
        rows_seen = 0
        for d in iter_days(start, TRADEFLOW_END):
            seen_days += 1
            ok, rows = parse_tradeflow_day(symbol, d)
            if ok:
                ok_days += 1
                rows_seen += rows
            if seen_days % 25 == 0:
                print(f"tradeflow progress {symbol}: {d} ok_days={ok_days}/{seen_days} raw_rows={rows_seen:,}", flush=True)
                save_status(symbol=symbol, phase="tradeflow", day=str(d), ok_days=ok_days, seen_days=seen_days)
            # public archive is CDN; keep it polite and RPi-friendly
            time.sleep(0.04)
        assemble_tradeflow_chunks(symbol, start, TRADEFLOW_END)
        print(f"{symbol}/tradeflow DONE ok_days={ok_days}/{seen_days} raw_rows={rows_seen:,}", flush=True)
        save_status(symbol=symbol, phase="tradeflow_done", ok_days=ok_days, seen_days=seen_days, raw_rows=rows_seen)


def main() -> int:
    print(f"FULL_HISTORY_START end={END.isoformat()} tradeflow_end={TRADEFLOW_END.isoformat()}", flush=True)
    save_status(started_at=datetime.now(timezone.utc).isoformat(), end=END.isoformat(), tradeflow_end=TRADEFLOW_END.isoformat())
    download_ohlc_oi_funding()
    download_tradeflow()
    save_status(done_at=datetime.now(timezone.utc).isoformat(), phase="done")
    print("FULL_HISTORY_DONE", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
