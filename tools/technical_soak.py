"""Real worker crash and backup recovery of technical state plus an ATR bracket."""

import argparse
from pathlib import Path
import sys
import time

from apps.openpine.bootstrap import local_manager
from packages.contracts import HistoricalBar
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json
from packages.storage.backup import backup, restore


SOURCE = '''//@version=6
strategy("Technical recovery",default_qty_type=strategy.fixed,initial_capital=1000)
length=input.int(3)
volatility=ta.atr(length)
smoothed=ta.rma(close,length)
oscillator=ta.rsi(close,length)
channelHigh=ta.highest(high,length)
channelLow=ta.lowest(low,length)
if bar_index==3
    strategy.entry("L",strategy.long)
    strategy.exit("X","L",stop=close-volatility*2,limit=close+volatility*5)
plot(volatility)
plot(smoothed)
plot(oscillator)
plot(channelHigh)
plot(channelLow)
plot(strategy.equity)
'''


def wait(manager, job, expected='succeeded', timeout=240):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        state = manager.get(job)
        if state['status'] == expected:
            return state
        if state['status'] in ('failed', 'cancelled', 'timed_out', 'succeeded'):
            raise AssertionError(state)
        time.sleep(.025)
    raise TimeoutError(state)


def checkpoint_state(manager, job, state, required_kinds=None):
    manifest = manager.services.checkpoints.manifest(state['checkpoint_id'], plan=manager.plan(job))
    runtime = manager.objects.get(manifest['runtime_ref'])
    bracket = runtime['simulation']['broker']['protection']
    if bracket is None or bracket['target_kind'] != 'fill':
        raise AssertionError('Durable checkpoint has no active exact-fill ATR bracket.')
    kinds = {s['kind'] for s in runtime['runtime']['states']}
    if not (required_kinds or {'rma', 'atr', 'rsi', 'highest', 'lowest'}) <= kinds:
        raise AssertionError('Durable checkpoint lacks the technical kernel states.')
    return {'checkpoint_id': state['checkpoint_id'], 'count': runtime['count'],
            'kinds': sorted(kinds), 'protection': bracket}


def run(directory, bars=30000, *, source=SOURCE, required_kinds=None, schema_version='openpine.technical-recovery.1'):
    root = Path(directory).resolve()
    if root.exists():
        raise ValueError('Use a new technical proof directory.')
    if type(bars) is not int or not 5000 <= bars <= 10000000:
        raise ValueError('Technical recovery requires 5000..10000000 bars.')
    root.mkdir(parents=True)
    policy = ResourcePolicy(workers=1, total_memory_mb=512, max_attempts=2)
    with local_manager(root / 'state', policy=policy) as manager:
        request = {'schema_version': 'openpine.job-request.2', 'timeout_seconds': 240,
            'input_ref': manager.freeze_input({'source': source, 'source_name': 'technical-recovery.pine'}),
            'snapshot_id': manager.market.ingest(
                {'context_id': 'technical.crash', 'symbol': 'TEST:TECHNICAL', 'timeframe': '1',
                 'bar_schema': 'openpine.historical-bars.2'},
                (HistoricalBar(i * 60000, 100., 100., 112. if i == bars - 1 else 101.,
                               94. if i == bars - 1 else 99., 1.) for i in range(bars)),
                {'provider': 'uploaded-file.1'})}
        baseline = manager.submit(request, checkpoint_interval_bars=0)['job_id']
        wait(manager, baseline)
        expected = manager.summary(baseline)['result']
        statistics = expected['simulation']['statistics']
        if (statistics['equity'], statistics['fills'], statistics['trades']) != ('996', 2, 1):
            raise AssertionError(statistics)
        job = manager.submit(request, checkpoint_interval_bars=1000)['job_id']
        deadline = time.monotonic() + 150
        while time.monotonic() < deadline:
            with manager.lock:
                state = manager.get(job)
                if state['status'] == 'running' and state.get('checkpoint_id'):
                    durable = checkpoint_state(manager, job, state,required_kinds)
                    manager.children[job][0].kill()
                    break
                if state['status'] in ('failed', 'cancelled', 'timed_out', 'succeeded'):
                    raise AssertionError('Worker completed or failed before the crash injection: '+str(state))
            time.sleep(.01)
        else:
            raise TimeoutError('Worker did not publish a technical checkpoint.')
        state = wait(manager, job)
        if state['attempt_count'] != 2 or state['resumed_from_bar'] < 1000:
            raise AssertionError(state)
        if manager.summary(job)['result'] != expected:
            raise AssertionError('Worker recovery changed technical outputs or protective journal.')
        worker = {'job_id': job, 'attempt_count': state['attempt_count'],
            'resumed_from_bar': state['resumed_from_bar'], 'durable': durable,
            'journal_and_parquet_identical': True, 'worker_killed': True}
        paused = manager.submit(request, pause_after=5, checkpoint_interval_bars=1000)['job_id']
        state = wait(manager, paused, 'paused')
        backup_boundary = checkpoint_state(manager, paused, state,required_kinds)
        backup(manager.db, manager.directory, root / 'backup')
    restore(root / 'backup', root / 'restored')
    with local_manager(root / 'restored', policy=policy) as manager:
        manager.resume(paused)
        state = wait(manager, paused)
        if state['resumed_from_bar'] != 5 or manager.summary(paused)['result'] != expected:
            raise AssertionError('Backup restore changed the joint technical/protective state.')
        restored = {'job_id': paused, 'resumed_from_bar': state['resumed_from_bar'],
            'durable': backup_boundary, 'journal_and_parquet_identical': True, 'backup_restore': True}
    receipt = {'schema_version': schema_version, 'status': 'passed',
        'python': '.'.join(map(str, sys.version_info[:3])), 'bars': bars,
        'worker': worker, 'backup': restored, 'statistics': statistics}
    atomic_json(root / 'evidence.json', receipt)
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--bars', type=int, default=30000)
    args = parser.parse_args()
    print(run(args.directory, args.bars))


if __name__ == '__main__':
    main()
