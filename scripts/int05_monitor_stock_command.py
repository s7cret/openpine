"""Operational resource measurements around an unchanged stock CLI command.

This records costs and cancels before the agreed disk floor. It never admits
test results or edits a stock primary. Each invocation has its own output root.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import resource
import signal
import time

from openpine.verification.execution_disk import ObservationWriter
from openpine.verification.execution_identity import hash_file, write_once_json
from openpine.verification.execution_process import start_declared_process, _stop_group
from openpine.verification.identity import seal


def process_tree_memory(parent: int) -> dict:
    rows = {}
    for item in Path('/proc').iterdir():
        if not item.name.isdigit():
            continue
        try:
            stat = (item / 'stat').read_text().rsplit(')', 1)[1].split()
            rows[int(item.name)] = (int(stat[1]), int(stat[21]) * os.sysconf('SC_PAGE_SIZE'))
        except (OSError, ValueError, IndexError):
            continue
    members = {parent}
    while True:
        expanded = members | {pid for pid, (ppid, _) in rows.items() if ppid in members}
        if expanded == members:
            break
        members = expanded
    return {'processes': len(members & rows.keys()),
            'rss_bytes': sum(rows[pid][1] for pid in members if pid in rows)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--cwd', type=Path, required=True)
    parser.add_argument('--minimum-free-bytes', type=int, default=20 * 1024**3)
    parser.add_argument('--soft-checkpoint-free-bytes', type=int)
    parser.add_argument('argv', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    argv = args.argv[1:] if args.argv[:1] == ['--'] else args.argv
    args.output.mkdir(parents=True, exist_ok=False)
    observations = ObservationWriter(args.output)
    tick = time.perf_counter()
    minimum_free = None
    initial_free = None
    soft_checkpoint_written = False
    peak_rss = 0
    floor_cancelled = False
    process = None
    with (args.output / 'stdout.log').open('xb') as out, (args.output / 'stderr.log').open('xb') as err, (args.output / 'process-memory.jsonl').open('x') as memory:
        while True:
            now = datetime.now(timezone.utc).isoformat()
            stats = os.statvfs(args.output)
            free = stats.f_bavail * stats.f_frsize
            minimum_free = free if minimum_free is None else min(minimum_free, free)
            initial_free = free if initial_free is None else initial_free
            if (args.soft_checkpoint_free_bytes is not None
                    and free < args.soft_checkpoint_free_bytes
                    and not soft_checkpoint_written):
                write_once_json(args.output / 'soft-disk-checkpoint.json', seal({
                    'kind': 'parent operational soft disk checkpoint; no test acceptance',
                    'observed_at': now, 'free_bytes': free,
                    'initial_free_bytes': initial_free,
                    'growth_bytes_since_start': max(0, initial_free - free),
                    'wall_seconds': time.perf_counter() - tick,
                    'peak_sampled_tree_rss_bytes': peak_rss,
                    'soft_threshold_bytes': args.soft_checkpoint_free_bytes,
                    'hard_floor_bytes': args.minimum_free_bytes,
                    'full_acceptance': False, 'retries': 0}))
                soft_checkpoint_written = True
            observations.append({'observed_at': now, 'free_bytes': free, 'error': None})
            # 64 MiB cancellation margin is additional to the parent operational floor.
            if free < args.minimum_free_bytes + 64 * 1024**2:
                floor_cancelled = True
                if process is not None and process.poll() is None:
                    # Allow stock run_logged to preserve cancellation receipts and
                    # clean up its separately declared subprocess group first.
                    os.kill(process.pid, signal.SIGINT)
                    try:
                        process.wait(timeout=5)
                    except TimeoutError:
                        _stop_group(process)
                    except Exception:  # noqa: BLE001 -- supervision must terminate its declared group
                        _stop_group(process)
                break
            if process is None:
                process = start_declared_process(argv, cwd=args.cwd, env=dict(os.environ), stdout=out, stderr=err, start_new_session=True)
            observed = process_tree_memory(process.pid)
            peak_rss = max(peak_rss, observed['rss_bytes'])
            memory.write(json.dumps({'observed_at': now, **observed, 'free_bytes': free, 'growth_bytes_since_start': max(0, initial_free - free)}) + '\n')
            memory.flush()
            if process.poll() is not None:
                break
            time.sleep(0.5)
    stream = observations.finish()
    returncode = process.poll() if process is not None else None
    report = seal({'kind': 'resource costs of unchanged stock command; no test acceptance',
                   'argv': argv, 'cwd': str(args.cwd), 'wall_seconds': time.perf_counter() - tick,
                   'returncode': returncode, 'floor_cancelled': floor_cancelled,
                   'minimum_free_bytes': minimum_free, 'required_floor_bytes': args.minimum_free_bytes,
                   'initial_free_bytes': initial_free,
                   'peak_growth_bytes_from_global_free': max(0, initial_free - minimum_free) if initial_free is not None else None,
                   'soft_checkpoint_threshold_bytes': args.soft_checkpoint_free_bytes,
                   'soft_checkpoint_written': soft_checkpoint_written,
                   'peak_sampled_tree_rss_bytes': peak_rss,
                   'resource_children_maxrss_kib': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss,
                   'cpu_user_seconds': resource.getrusage(resource.RUSAGE_CHILDREN).ru_utime,
                   'cpu_system_seconds': resource.getrusage(resource.RUSAGE_CHILDREN).ru_stime,
                   'disk': {'observations': observations.observations, 'observation_stream': stream},
                   'files': {p.name: hash_file(p) for p in args.output.iterdir() if p.is_file()},
                   'retries': 0, 'full_acceptance': False})
    write_once_json(args.output / 'measurement.json', report)
    print(json.dumps({k: report[k] for k in ('content_hash', 'wall_seconds', 'returncode', 'floor_cancelled', 'minimum_free_bytes', 'peak_sampled_tree_rss_bytes')}, indent=2), flush=True)
    return 0 if returncode == 0 and not floor_cancelled else 1


if __name__ == '__main__':
    raise SystemExit(main())
