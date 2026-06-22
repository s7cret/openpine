#!/usr/bin/env python3
"""Launch candles-only LINK/AVAX optimizer as soon as both full kline series exist.

This is intentionally separate from run_optimizer_after_full_download.py: the full
hybrid watcher waits for tradeflow/OI/funding completion, which can take much
longer than minute-candle availability.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import subprocess
import time

REPO = Path('/home/moltbot1/.openclaw/workspace/openpine')
ROOT = REPO / 'research/microstructure-v2'
CACHE = ROOT / 'data/cache'
STATUS = ROOT / 'artifacts/full_history_link_avax/DOWNLOAD_STATUS.json'
OPT = ROOT / 'scripts/link_avax_multitf_optimizer.py'
ART_NAME = 'link_avax_candles_only_optimizer'
ART = ROOT / 'artifacts' / ART_NAME
LOG = ART / 'FULL_RUN.log'
SENTINEL = ART / '.kline_optimizer_done'
SYMBOL_STARTS = {
    'AVAXUSDT': datetime(2021, 9, 15, tzinfo=timezone.utc),
    'LINKUSDT': datetime(2018, 1, 1, tzinfo=timezone.utc),
}
POLL_SEC = 120
TIMEOUT_SEC = 18 * 3600


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text(encoding='utf-8'))
    except Exception:
        return {}


def month_ranges(start: datetime, end: datetime):
    cur = datetime(start.year, start.month, 1, tzinfo=timezone.utc)
    if cur < start:
        cur = start
    while cur < end:
        nxt = datetime(cur.year + 1, 1, 1, tzinfo=timezone.utc) if cur.month == 12 else datetime(cur.year, cur.month + 1, 1, tzinfo=timezone.utc)
        yield cur, min(nxt, end)
        cur = nxt


def kline_missing(end: datetime) -> dict[str, list[str]]:
    missing: dict[str, list[str]] = {}
    for symbol, start in SYMBOL_STARTS.items():
        miss = []
        for a, b in month_ranges(start, end):
            name = f'kline_{a:%Y%m%d}_{b:%Y%m%d}.parquet'
            if not (CACHE / symbol / name).exists():
                miss.append(name)
        missing[symbol] = miss
    return missing


def main() -> int:
    ART.mkdir(parents=True, exist_ok=True)
    if SENTINEL.exists():
        print(f'CANDLES_OPTIMIZER_ALREADY_DONE {ART}', flush=True)
        return 0
    started = time.time()
    print('CANDLES_WATCHER_START', flush=True)
    while time.time() - started < TIMEOUT_SEC:
        status = read_status()
        end_raw = status.get('end')
        if not end_raw:
            print('waiting: no end in status', flush=True)
            time.sleep(POLL_SEC)
            continue
        end = datetime.fromisoformat(str(end_raw))
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        missing = kline_missing(end)
        total_missing = sum(len(v) for v in missing.values())
        print(
            f"watch_kline missing={total_missing} "
            f"AVAX={len(missing['AVAXUSDT'])} LINK={len(missing['LINKUSDT'])} "
            f"status={status.get('symbol')}/{status.get('phase')}/{status.get('chunk')}",
            flush=True,
        )
        if total_missing == 0:
            cmd = [
                '/usr/bin/python3.13', str(OPT),
                '--symbols', 'AVAXUSDT', 'LINKUSDT',
                '--timeframes', '5m', '15m', '1h', '4h',
                '--candles-only',
                '--artifact-dir', ART_NAME,
                '--quick-max', '200',
                '--max-base', '120',
            ]
            print('CANDLES_OPTIMIZER_START ' + ' '.join(cmd), flush=True)
            with LOG.open('w', encoding='utf-8') as fh:
                cp = subprocess.run(cmd, cwd=str(REPO), stdout=fh, stderr=subprocess.STDOUT, text=True)
            print(f'CANDLES_OPTIMIZER_DONE exit={cp.returncode} log={LOG}', flush=True)
            if cp.returncode == 0:
                SENTINEL.write_text(datetime.now(timezone.utc).isoformat() + '\n', encoding='utf-8')
            try:
                print('\n'.join(LOG.read_text(encoding='utf-8').splitlines()[-80:]), flush=True)
            except Exception:
                pass
            return cp.returncode
        time.sleep(POLL_SEC)
    print('CANDLES_WATCHER_TIMEOUT', flush=True)
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
