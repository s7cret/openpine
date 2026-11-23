"""Kill a strategy worker during a journal append while a second job is queued."""
import argparse
import platform
import time
from pathlib import Path

from apps.openpine.bootstrap import local_manager
from packages.contracts import HistoricalBar
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json
from packages.execution.identity import implementation_hash
from tools.managed_recovery_soak import until


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    root.mkdir(parents=True,exist_ok=False)
    count = 8000
    strategy = """//@version=6
strategy("Recovery",default_qty_type=strategy.fixed,default_qty_value=1,initial_capital=1000)
if bar_index%4==0
    strategy.entry("L",strategy.long)
if bar_index%4==2
    strategy.close("L")
plot(strategy.position_size)"""
    with local_manager(root/'state',policy=ResourcePolicy(workers=1,max_attempts=3)) as manager:
        snapshot = manager.market.ingest(dict(context_id='mixed',symbol='TEST:MIXED',timeframe='1',
            bar_schema='openpine.historical-bars.2'),(HistoricalBar(i*60000,100.,100.,100.,100.,1.)
            for i in range(count)),dict(provider='generated.1'))
        def submit(text):
            ref = manager.freeze_input(dict(source=text,source_name='mixed.pine'))
            return manager.submit(dict(schema_version='openpine.job-request.2',input_ref=ref,
                snapshot_id=snapshot,timeout_seconds=360),checkpoint_interval_bars=1000)['job_id']
        job = submit(strategy)
        other = submit('//@version=6\nindicator("Queued")\nplot(close)')
        def interrupt():
            with manager.lock:
                state = manager.get(job)
                if state['status'] in ('succeeded','failed','cancelled','timed_out'):
                    raise AssertionError(state)
                if not state['checkpoint_id'] or job not in manager.children:
                    return
                child, _, _, directory = manager.children[job][:4]
                journal = next(directory.rglob('journal.csv'),None)
                if not journal:
                    return
                size = journal.stat().st_size
                time.sleep(.06)
                if not journal.exists() or journal.stat().st_size <= size:
                    return
                assert manager.get(other)['status']=='queued'
                child.kill()
                return dict(worker=child.pid,checkpoint=state['checkpoint_id'],journal_bytes=size)
        cut = until(interrupt)
        for key in (job,other):
            def finished():
                status=manager.get(key)['status']
                if status in ('failed','cancelled','timed_out'):
                    raise AssertionError(manager.get(key))
                return status=='succeeded'
            until(finished)
        state = manager.get(job)
        assert state['attempt_count']==2 and state['resumed_from_bar']>=1000 and manager.get(other)['attempt_count']==1
        journal = manager.summary(job)['result']['simulation']['journal']
        fills, trades = set(), set()
        for event in manager.queries.read_events(manager.objects,journal,kinds=('fill','trade')):
            value = event['value']
            if event['kind']=='fill':
                assert value['fill_id'] not in fills
                fills.add(value['fill_id'])
            else:
                assert value['trade_id'] not in trades and value['entry_fill_id'] in fills and value['exit_fill_id'] in fills
                assert event['bar_index']==len(trades)*4+3 and value['quantity']=='1'
                trades.add(value['trade_id'])
        assert len(fills)==count//2 and len(trades)==count//4
        assert manager.summary(other)['result']['outputs'][0]['count']==count
        manager.db.check()
        report=dict(status='passed',python=platform.python_version(),implementation_sha256=implementation_hash(),
                    bars=count,cut=cut,resumed_from_bar=state['resumed_from_bar'],unique_fills=len(fills),
                    unique_trades=len(trades),indicator_attempts=1,strategy_attempts=2)
        atomic_json(root/'report.json',report)
        print(report)


if __name__=='__main__':
    main()
