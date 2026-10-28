"""Mixed history/research/replay, crash, full backup, GC and identical continuation."""
import argparse,errno,json,threading,time,sys
from dataclasses import asdict
from pathlib import Path
from apps.openpine.bootstrap import local_manager,local_services
from apps.openpine.experiments import Experiments
from apps.openpine.replay import Replays
from packages.contracts import OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json,read_json
from packages.execution.identity import implementation_hash,build_identity
from packages.storage.backup import backup,restore
from packages.storage.disk import DiskBusy
from packages.storage.results import series_rows
from tools.managed_recovery_soak import run as crash_run,until
from tools.unified_storage_soak import verify_persisted

def retry(call):
    def attempt():
        try:return call() or True
        except OpenPineError as error:
            if error.diagnostic.code!='COORDINATOR_BUSY':raise
    return until(attempt,300)

def done(manager,job):
    state=manager.get(job)
    if state['status'] in ('failed','cancelled','timed_out'):raise AssertionError(state)
    return state if state['status']=='succeeded' else None

def replay_values(service,key,bars):
    state=service.status(key);rows=list(series_rows(service.manager.objects,
        {'observation_count':state['confirmed_bars'],'series':state['series']},0,bars))
    if len(rows)!=bars or any(index!=i or timestamp!=i*60000 or value!=float(i%32+12)
            for i,(index,timestamp,site,value) in enumerate(rows)):raise AssertionError('Replay values differ after full-store restore.')
    return rows

def run(directory,bars=100000):
    root=Path(directory).resolve();root.mkdir(parents=True,exist_ok=False);start=time.monotonic()
    crashed=crash_run(root/'crash',bars);store=root/'crash/store'
    original=read_json(root/'crash/job.json')['job_id'];policy=ResourcePolicy(workers=2,total_memory_mb=1024,max_attempts=3)
    with local_manager(store,policy=policy) as manager:
        original_result=manager.get(original)['result_id']
        pending=manager.submit(manager.request(original),checkpoint_interval_bars=1000,pause_after=bars//2)['job_id']
        ref=manager.freeze_input({'source':'//@version=6\nindicator("mixed")\nn=input.int(1)\nplot(close+n)','source_name':'mixed.pine'})
        experiments=Experiments(manager)
        try:
            experiment=experiments.create(dict(name='mixed',input_ref=ref,snapshot_id=manager.plan(original).snapshot_id,
                timeout_seconds=3600,variants=[{'n':1},{'n':2}],checkpoint_interval_bars=5000))['experiment_id']
            replays=Replays(manager);context=MarketContext('primary','TEST:P','1','mixed.1')
            replay=replays.create(dict(input_ref=ref,contexts=[asdict(context)],primary_id='primary',broker_settings=None,timeout_seconds=300))['replay_id']
            tape=[];live_bars=2048
            for i in range(live_bars):
                op=float(i%32+10)
                for revision,kind in enumerate(('open','update','close')):
                    tape.append(MarketEvent(len(tape),'primary',kind,i*60000,(i+1)*60000 if kind=='close' else i*60000+revision,
                        revision,op,op+1,op,op+1 if kind=='close' else op,float(revision)).payload())
            for offset in range(0,len(tape),1000):replays.append(replay,tape[offset:offset+1000])
            # The retrying live demand competes with both persistent job lanes.
            retry(lambda:replays.advance(replay,max_events=3072,checkpoint_every=512))
            until(lambda:manager.get(pending)['status']=='paused')
            research=until(lambda:(s if s['status']=='succeeded' else None) if (s:=experiments.status(experiment)) else None)
            research_ids=[manager.get(v['job_id'])['result_id'] for v in research['variants']]
            # Admission refusal preserves the committed prefix.
            previous=replays.repository.row(replay)[1]
            from unittest.mock import patch
            def full(*args,**kwargs):raise DiskBusy({'reason':'disk','retryable':True,'retry_after_ms':1000})
            with patch.object(manager.limiter.disk,'reserve',side_effect=full):
                try:replays.advance(replay,max_events=3)
                except OpenPineError as error:
                    if error.admission['reason']!='disk':raise
                else:raise AssertionError('Disk shortage was bypassed.')
            if replays.repository.row(replay)[1]!=previous:raise AssertionError('Disk refusal replaced a committed prefix.')
            # A real ENOSPC during worker publication must also keep that root.
            import tempfile
            from apps.replay_worker import execute
            from packages.storage.objects import ObjectStore
            with tempfile.TemporaryDirectory(dir=store,prefix='failed-publication-') as attempt:
                with patch.object(ObjectStore,'publish',side_effect=OSError(errno.ENOSPC,'injected full disk')):
                    try:execute(store,replay,Path(attempt),max_events=3,checkpoint_every=1)
                    except OSError as error:
                        if error.errno!=errno.ENOSPC:raise
                    else:raise AssertionError('Full-disk publication unexpectedly succeeded.')
            if replays.repository.row(replay)[1]!=previous:raise AssertionError('Failed publication replaced a committed prefix.')
            backup(manager.db,store,root/'backup')
            # Continue the original as an independent oracle for result IDs.
            manager.resume(pending);until(lambda:done(manager,pending))
            expected=manager.get(pending)['result_id']
            retry(lambda:replays.advance(replay,max_events=10000,checkpoint_every=512))
            wanted=replay_values(replays,replay,live_bars)
            expected_replay=replays.repository.row(replay)[1]
        finally:experiments.close()
    restore(root/'backup',root/'restored')
    with local_services(root/'restored') as services:
        orphan=services.objects.put({'unreachable':'recovery-proof'})
        collected=services.lifecycle.collect(apply=True,grace_seconds=0)
        if orphan not in collected['candidates']:raise AssertionError('GC proof did not collect its orphan.')
    with local_manager(root/'restored',policy=policy) as manager:
        if manager.get(original)['result_id']!=original_result:raise AssertionError('Published result identity changed on restore.')
        research=Experiments(manager,start=False).status(experiment)
        if [manager.get(v['job_id'])['result_id'] for v in research['variants']]!=research_ids:raise AssertionError('Research result identities changed.')
        manager.resume(pending);until(lambda:done(manager,pending))
        if manager.get(pending)['result_id']!=expected:raise AssertionError('Continued run identity differs from original-store continuation.')
        points=verify_persisted(manager.objects,manager.summary(pending)['result'],bars)
        service=Replays(manager);retry(lambda:service.advance(replay,max_events=10000,checkpoint_every=512))
        if replay_values(service,replay,live_bars)!=wanted or service.repository.row(replay)[1]!=expected_replay:
            raise AssertionError('Replay continuation changed bytes or values.')
        manager.services.lifecycle.inspect(grace_seconds=0)
    report=dict(status='passed',python=sys.version.split()[0],bars=bars,crash=crashed,verified_parquet_values=points,
        replay_bars=live_bars,research_variants=2,history_result_id=expected,replay_checkpoint_id=expected_replay,
        backup_restored_gc_continued=True,disk_refusal_preserved_prefix=True,
        seconds=round(time.monotonic()-start,3),implementation_sha256=implementation_hash(),build=build_identity())
    atomic_json(root/'report.json',report);return report

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--directory',type=Path,required=True);p.add_argument('--bars',type=int,default=100000);a=p.parse_args()
    if sys.version_info[:3]!=(3,14,8) or a.bars<20000:raise ValueError('Use Python 3.14.8 and >=20000 bars.')
    print(json.dumps(run(a.directory,a.bars)))
if __name__=='__main__':main()
