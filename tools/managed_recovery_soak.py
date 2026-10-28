"""Long actual JobManager/apps.worker recovery, with persisted-value oracle."""
import argparse,json,os,subprocess,sys,time
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.contracts import HistoricalBar
from packages.contracts.contexts import MarketContext
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json,read_json
from packages.execution.identity import implementation_hash,build_identity
from tools.unified_storage_soak import source,verify_persisted

def until(call,seconds=300):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        value=call()
        if value:return value
        time.sleep(.02)
    raise TimeoutError('Managed recovery proof timed out.')

def seed(root,bars):
    policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=3)
    with local_manager(root/'store',policy=policy) as manager:
        snapshots=[]
        for context,symbol,base in [('primary','TEST:P',0),('a','TEST:A',100),('b','TEST:B',200)]:
            metadata=dict(context_id=context,symbol=symbol,timeframe='1',bar_schema='openpine.historical-bars.2')
            rows=(HistoricalBar(i*60000,float(base+i%32+1),float(base+i%32+1),float(base+i%32+1),float(base+i%32+1),10.) for i in range(bars))
            snapshots.append(manager.market.ingest(metadata,rows,{'provider':'managed-proof.1'}))
        ref=manager.freeze_input({'source':source(bars),'source_name':'managed-proof.pine'})
        contexts=[MarketContext(k,s,'1','managed-proof.1') for k,s in [('primary','TEST:P'),('a','TEST:A'),('b','TEST:B')]]
        from dataclasses import asdict
        request=dict(schema_version='openpine.job-request.4',input_ref=ref,snapshot_id=snapshots[0],timeout_seconds=3600,
            primary_context=asdict(contexts[0]),contexts=[dict(context=asdict(c),snapshot_id=sha) for c,sha in zip(contexts[1:],snapshots[1:])])
        job=manager.submit(request,checkpoint_interval_bars=5000)['job_id']
        atomic_json(root/'job.json',{'job_id':job,'implementation':implementation_hash()})
        until(lambda:manager.get(job)['attempt_count']==1)
        with manager.lock:
            state=manager.get(job);path=manager.children[job][3]
            envelope=until(lambda:read_json(path/'checkpoint.json') if (path/'checkpoint.json').exists() else None)
            manager.children[job][0].kill()
            atomic_json(root/'worker-killed.json',dict(state=state,published=envelope,pid=state['worker_pid']))
        until(lambda:manager.get(job)['attempt_count']==2)
        with manager.lock:
            state=manager.get(job);path=manager.children[job][3]
            envelope=until(lambda:read_json(path/'checkpoint.json') if (path/'checkpoint.json').exists() else None)
            # Parent kills this coordinator while its lock prevents catalog
            # acknowledgement. Restart must capture the fenced disk envelope.
            atomic_json(root/'coordinator-ready.json',dict(state=state,published=envelope,pid=os.getpid()))
            while True:time.sleep(.2)

def run(root,bars=100000):
    root=Path(root).resolve();root.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    with (root/'coordinator.log').open('wb') as log:
        child=subprocess.Popen([sys.executable,'-X','utf8','-m','tools.managed_recovery_soak','--directory',str(root),'--bars',str(bars),'--seed'],stdout=log,stderr=log)
        try:
            def ready():
                if child.poll() is not None:raise AssertionError('Coordinator exited; inspect coordinator.log.')
                return (root/'coordinator-ready.json').exists()
            until(ready);child.kill();child.wait(timeout=10)
        finally:
            if child.poll() is None:child.kill();child.wait(timeout=10)
    saved=read_json(root/'job.json');killed=read_json(root/'worker-killed.json');ready=read_json(root/'coordinator-ready.json')
    if saved['implementation']!=implementation_hash():raise AssertionError('Implementation changed between attempts.')
    # The worker's parent watchdog has a bounded grace; lease acquisition also
    # excludes the dead coordinator before accepting the new one.
    with local_manager(root/'store',policy=ResourcePolicy(workers=1,total_memory_mb=512,max_attempts=3)) as manager:
        job=saved['job_id']
        def completed():
            state=manager.get(job)
            if state['status'] in ('failed','cancelled','timed_out'):raise AssertionError(state)
            return state if state['status']=='succeeded' else None
        state=until(completed)
        if state['attempt_count']!=3 or state['resumed_from_bar']!=ready['published']['completed']:raise AssertionError(state)
        summary=manager.summary(job)['result'];points=verify_persisted(manager.objects,summary,bars)
        refs=set();from packages.storage.checkpoint_tree import references
        manifest=manager.services.checkpoints.manifest(state['checkpoint_id'])
        references(manager.objects,manifest['runtime_ref'],refs.add)
        reachable=manager.services.lifecycle.inspect(grace_seconds=0)
        atomic_json(root/'lifecycle.json',reachable)
        report=dict(status='passed',python=sys.version.split()[0],bars=bars,verified_parquet_values=points,
            attempts=state['attempt_count'],worker_killed=killed['pid'],coordinator_killed=ready['pid'],final_worker=state['worker_pid'],
            resumed_from_bar=state['resumed_from_bar'],checkpoint_block_objects=len(refs),resources=state.get('resources'),
            seconds=round(time.monotonic()-start,3),implementation_sha256=implementation_hash(),build=build_identity())
        atomic_json(root/'report.json',report);return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=100000);p.add_argument('--seed',action='store_true');a=p.parse_args()
    if sys.version_info[:3]!=(3,14,8) or a.bars<20000:raise ValueError('Use Python 3.14.8 and at least 20000 bars.')
    if a.seed:seed(a.directory,a.bars)
    else:print(json.dumps(run(a.directory,a.bars)))
if __name__=='__main__':main()
