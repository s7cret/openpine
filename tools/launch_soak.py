"""Prepared Parquet graph through a million-bar worker and real recovery boundaries."""
import argparse
from pathlib import Path
import sys
import time
import uuid

from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.launches import Launches
from packages.contracts import HistoricalBar
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json,digest
from packages.execution.identity import implementation_hash,build_identity
from packages.storage.backup import backup,restore

SOURCE='''//@version=6
strategy("Prepared worker recovery",default_qty_type=strategy.fixed,initial_capital=1000)
length=input.int(3)
[main,signal,histogram]=request.security("TEST:LOAD","4",ta.macd(close,2,4,3))
volatility=ta.atr(length)
if bar_index==3
    strategy.entry("L",strategy.long)
    strategy.exit("X","L",stop=close-volatility*2,limit=close+volatility*5)
plot(main)
plot(histogram)
plot(strategy.equity)
'''


def wait(manager,key,expected='succeeded',timeout=3600):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=manager.get(key)
        if state['status']==expected:return state
        if state['status'] in ('failed','cancelled','timed_out','succeeded','paused'):raise AssertionError(state)
        time.sleep(.1)
    raise TimeoutError(state)


def run(directory,bars=1000000,*,source=SOURCE,instrument=None):
    root=Path(directory).resolve()
    if root.exists() or type(bars) is not int or bars<10000 or bars%4:raise ValueError('Use a new directory and at least 10000 bars divisible by four.')
    root.mkdir(parents=True);started=time.monotonic();implementation=implementation_hash();build=build_identity()
    policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=2)
    def services(manager):
        history=HistoryJobs(manager,start=False)
        return history,Launches(manager,history)
    def prepare(manager,service,count,pause=None,interval=100000):
        snapshots=[]
        for tf,size in (('1',count),('4',count//4)):
            snapshots.append(manager.market.ingest({'context_id':'load.'+tf,'symbol':'TEST:LOAD','timeframe':tf,
                'bar_schema':'openpine.historical-bars.2'},
                (HistoricalBar(i*int(tf)*60000,100.,112. if i==size-1 else 100.,113. if i==size-1 else 101.,99.,1.) for i in range(size)),
                {'provider':'uploaded-file.1',**({'environment':{'schema_version':'openpine.instrument-environment.1','instrument':instrument}} if instrument is not None else {})}))
        config={'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,
            'input_ref':manager.freeze_input({'source':source,'source_name':'prepared-worker.pine'}),
            'primary':{'kind':'snapshot','snapshot_id':snapshots[0]},'contexts':[{'kind':'snapshot','snapshot_id':snapshots[1]}],
            'inputs':{},'timeout_seconds':3600,'gap_policy':'fail','checkpoint_interval_bars':interval}
        if pause:config['pause_after_bars']=pause
        ready=service.create(config)
        if ready['status']!='ready':raise AssertionError(ready)
        service.start(config['request_id'])
        return config['request_id'],ready
    with local_manager(root/'million',policy=policy) as manager:
        history,service=services(manager)
        try:
            million,ready=prepare(manager,service,bars,pause=10000)
            paused=wait(manager,million,'paused')
            million_plan=manager.plan(million).payload()
            atomic_json(root/'progress.json',{'phase':'million_paused','bars':bars,'job_id':million})
        finally:history.close()
    with local_manager(root/'million',policy=policy) as manager:
        history,service=services(manager)
        try:
            if service.status(million)['prepared_ref']!=ready['prepared_ref']:raise AssertionError('Preparation changed after coordinator restart.')
            manager.resume(million);state=wait(manager,million)
            summary=manager.summary(million)['result']
            if state['completed']!=bars or state['resumed_from_bar']!=10000:raise AssertionError(state)
            chart=manager.chart(million,start=bars-10,limit=10)
            if len(chart['bars'])!=10 or summary['simulation']['statistics']['trades']!=1:raise AssertionError(summary)
            million_receipt={'job_id':million,'plan':million_plan,'prepared_ref':ready['prepared_ref'],
                'datasets':ready['prepared']['datasets'],'completed':state['completed'],'resumed_from_bar':state['resumed_from_bar'],
                'summary_sha256':digest(summary),'result_sha256':state['result_sha256'],'statistics':summary['simulation']['statistics']}
            atomic_json(root/'progress.json',{'phase':'million_passed','job_id':million,'completed':bars})
        finally:history.close()
    # Separate 100k recovery corpus keeps three full-result comparisons bounded.
    recovery_bars=min(bars,100000)
    with local_manager(root/'recovery',policy=policy) as manager:
        history,service=services(manager)
        try:
            baseline,_=prepare(manager,service,recovery_bars);wait(manager,baseline)
            expected=manager.summary(baseline)['result']
            crashed,_=prepare(manager,service,recovery_bars,interval=1000)
            deadline=time.monotonic()+180
            while time.monotonic()<deadline:
                with manager.lock:
                    state=manager.get(crashed)
                    if state['status']=='running' and state.get('checkpoint_id'):
                        checkpoint=state['checkpoint_id'];manager.children[crashed][0].kill();break
                time.sleep(.02)
            else:raise TimeoutError('No checkpoint before worker crash.')
            state=wait(manager,crashed)
            if state['attempt_count']!=2 or state['resumed_from_bar']<1000 or manager.summary(crashed)['result']!=expected:raise AssertionError(state)
            worker_receipt={'job_id':crashed,'checkpoint_id':checkpoint,'attempt_count':state['attempt_count'],'resumed_from_bar':state['resumed_from_bar'],'identical_summary':True}
            restored,ready=prepare(manager,service,recovery_bars,pause=17,interval=1000);wait(manager,restored,'paused')
            backup(manager.db,manager.directory,root/'backup')
        finally:history.close()
    restore(root/'backup',root/'restored')
    with local_manager(root/'restored',policy=policy) as manager:
        history,service=services(manager)
        try:
            if service.status(restored)['prepared_ref']!=ready['prepared_ref']:raise AssertionError('Backup lost preparation.')
            manager.resume(restored);state=wait(manager,restored)
            if state['resumed_from_bar']!=17 or manager.summary(restored)['result']!=expected:raise AssertionError(state)
        finally:history.close()
    if implementation_hash()!=implementation or build_identity()!=build:raise AssertionError('Source changed during acceptance; repeat on stable source.')
    receipt={'schema_version':'openpine.launch-soak.1','status':'passed','python':sys.version.split()[0],
        'implementation_sha256':implementation,'build':build,'million':million_receipt,
        'recovery_bars':recovery_bars,'baseline_job_id':baseline,'worker_crash':worker_receipt,
        'backup':{'job_id':restored,'resumed_from_bar':17,'identical_summary':True},'seconds':round(time.monotonic()-started,3)}
    atomic_json(root/'evidence.json',receipt);return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--bars',type=int,default=1000000)
    args=parser.parse_args();print(run(args.directory,args.bars))


if __name__=='__main__':main()
