from packages.storage.checkpoint_records import get as checkpoint_get
"""Kill a coordinator at a durable pending/active bracket; restore and compare."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from apps.openpine.bootstrap import local_manager
from packages.compiler.semantic.specification import ROOT
from packages.contracts.wire import atomic_json,read_json
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore


def await_state(manager,job,status,timeout=150):
    deadline=time.monotonic()+timeout
    while time.monotonic()<deadline:
        state=manager.get(job)
        if state['status']==status:return state
        if state['status'] in ('failed','cancelled','succeeded'):raise AssertionError(state)
        time.sleep(.03)
    raise TimeoutError(state)


def child(root,cut):
    with local_manager(root/'state') as manager:
        fixture=ROOT/'conformance/exits'
        request={'schema_version':'openpine.job-request.2','timeout_seconds':120,
            'input_ref':manager.freeze_input({'source':(fixture/'v6-bracket.pine').read_text(encoding='utf-8'),'source_name':'protected.pine'}),
            'snapshot_id':manager.market.save(input_snapshot(read_json(fixture/'bars.json')))}
        baseline=manager.submit(request)['job_id'];await_state(manager,baseline,'succeeded')
        expected=manager.summary(baseline)['result']
        count=1 if cut=='pending' else 2
        job=manager.submit(request,pause_after=count,checkpoint_interval_bars=1)['job_id'];state=await_state(manager,job,'paused')
        checkpoint=manager.services.checkpoints.manifest(state['checkpoint_id'],plan=manager.plan(job))
        runtime=checkpoint_get(manager.objects,checkpoint['runtime_ref']);bracket=runtime['simulation']['broker']['protection']
        if bracket['target_kind']!=('pending' if count==1 else 'fill'):raise AssertionError(bracket)
        with manager.lock:
            atomic_json(root/'ready.json',{'job_id':job,'count':count,'expected':expected,'bracket':bracket,'checkpoint_id':state['checkpoint_id']})
            while True:time.sleep(.1)


def run(directory,cut='active'):
    root=Path(directory).resolve()
    if root.exists():raise ValueError('Use a new protection proof directory.')
    root.mkdir(parents=True)
    with (root/'coordinator.log').open('wb') as log:
        process=subprocess.Popen([sys.executable,'-X','utf8','-m','tools.protection_soak','--directory',str(root),'--cut',cut,'--child'],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+180
            while not (root/'ready.json').exists():
                if process.poll() is not None:raise AssertionError((root/'coordinator.log').read_text(encoding='utf-8',errors='replace')[-10000:])
                if time.monotonic()>deadline:raise TimeoutError('Coordinator did not reach protective checkpoint.')
                time.sleep(.04)
            process.kill();process.wait(timeout=10)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
    saved=read_json(root/'ready.json');job=saved['job_id']
    with local_manager(root/'state') as manager:backup(manager.db,manager.directory,root/'backup')
    restore(root/'backup',root/'restored')
    with local_manager(root/'restored') as manager:
        manager.resume(job);state=await_state(manager,job,'succeeded');actual=manager.summary(job)['result']
        if actual!=saved['expected'] or state['resumed_from_bar']!=saved['count']:raise AssertionError('Protected ledger/resume cursor changed.')
        statistics=actual['simulation']['statistics']
        if (statistics['equity'],statistics['fills'],statistics['trades'])!=('995',2,1):raise AssertionError(statistics)
        receipt={'schema_version':'openpine.protection-recovery.1','status':'passed','cut':cut,'python':'.'.join(map(str,sys.version_info[:3])),
            'job_id':job,'checkpoint_id':saved['checkpoint_id'],'resumed_from_bar':state['resumed_from_bar'],
            'journal_and_parquet_identical':True,'fills':2,'trades':1,'backup_restore':True,'coordinator_killed':True}
    atomic_json(root/'evidence.json',receipt)
    return receipt


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--cut',choices=('pending','active'),default='active');parser.add_argument('--child',action='store_true');args=parser.parse_args()
    if args.child:child(args.directory,args.cut)
    else:print(run(args.directory,args.cut))


if __name__=='__main__':main()
