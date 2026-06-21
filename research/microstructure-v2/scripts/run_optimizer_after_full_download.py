#!/usr/bin/env python3
"""Wait for AVAX/LINK full-history download, then run multi-TF optimizer."""
from __future__ import annotations

from pathlib import Path
import json
import os
import subprocess
import sys
import time

REPO = Path('/home/moltbot1/.openclaw/workspace/openpine')
STATUS = REPO / 'research/microstructure-v2/artifacts/full_history_link_avax/DOWNLOAD_STATUS.json'
OPT = REPO / 'research/microstructure-v2/scripts/link_avax_multitf_optimizer.py'
LOG = REPO / 'research/microstructure-v2/artifacts/link_avax_multitf_optimizer/FULL_RUN.log'
DOWNLOADER_PID = 1528167
TIMEOUT_SEC = 36 * 3600


def read_status() -> dict:
    try:
        return json.loads(STATUS.read_text())
    except Exception:
        return {}


def pid_alive(pid: int) -> bool:
    return Path(f'/proc/{pid}').exists()


def main() -> int:
    start = time.time()
    print(f'WATCHER_START downloader_pid={DOWNLOADER_PID}', flush=True)
    while time.time() - start < TIMEOUT_SEC:
        st = read_status()
        phase = st.get('phase')
        print(f"watch phase={phase} symbol={st.get('symbol')} chunk={st.get('chunk')} updated={st.get('updated_at')}", flush=True)
        if phase == 'done':
            LOG.parent.mkdir(parents=True, exist_ok=True)
            cmd = [
                '/usr/bin/python3.13', str(OPT),
                '--symbols', 'AVAXUSDT', 'LINKUSDT',
                '--timeframes', '5m', '15m', '1h', '4h',
                '--max-base', '120',
                '--quick-max', '200',
            ]
            print('OPTIMIZER_START ' + ' '.join(cmd), flush=True)
            with LOG.open('w', encoding='utf-8') as fh:
                cp = subprocess.run(cmd, cwd=str(REPO), stdout=fh, stderr=subprocess.STDOUT, text=True)
            print(f'OPTIMIZER_DONE exit={cp.returncode} log={LOG}', flush=True)
            try:
                print('\n'.join(LOG.read_text(encoding='utf-8').splitlines()[-80:]), flush=True)
            except Exception:
                pass
            return cp.returncode
        if not pid_alive(DOWNLOADER_PID):
            print(f'DOWNLOADER_EXITED_BEFORE_DONE status={st}', flush=True)
            return 2
        time.sleep(300)
    print('WATCHER_TIMEOUT', flush=True)
    return 3


if __name__ == '__main__':
    raise SystemExit(main())
