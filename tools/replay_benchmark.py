"""Offline observed tape, midbar backup, real coordinator crash and exact replay."""

import argparse
import hashlib
import os
import subprocess
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path

from apps.openpine.bootstrap import local_manager
from apps.openpine.replay import Replays
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import atomic_json, canonical
from packages.storage.backup import backup, restore
from packages.storage.database import Database
from packages.storage.replay import ReplayRepository
from packages.storage.objects import ObjectStore
from packages.storage.results import series_rows
from packages.storage.trades import event_rows
from packages.execution.identity import build_identity, implementation_hash
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.execution.realtime import ReplayEngine, Capture
from packages.execution.session import Session

PRIMARY=MarketContext('primary','TEST:REPLAY','1','recorded.1')
SOURCE='''//@version=6
strategy("replay scale",default_qty_type=strategy.fixed,initial_capital=1000000,commission_value=0.1)
if bar_index % 2 == 0
    strategy.entry("L",strategy.long)
else
    strategy.close("L")
plot(strategy.equity)
plot(ta.ema(close,20))
'''


def events(bars):
    for index in range(bars):
        t=index*60000;p=100.+index%7
        for revision,kind,at,c,v in ((0,'open',t,p,0.),(1,'update',t+30000,p+.25,1.),(2,'close',t+60000,p+.5,2.)):
            yield MarketEvent(index*3+revision,'primary',kind,t,at,revision,p,c,p,c,v).payload()


def hashes(manager,state):
    def hashed(rows):
        value=hashlib.sha256();count=0
        for row in rows:value.update(canonical(row)+b'\n');count+=1
        return {'rows':count,'sha256':value.hexdigest()}
    return {'series':hashed(series_rows(manager.objects,{'series':state['series'],'observation_count':state['confirmed_bars']})),
        'ledger':hashed(event_rows(manager.objects,state['simulation']['journal']))}


def rollback_proof(bars):
    """Large histories and stateful kernels must stay unchanged by previews."""
    source='//@version=6\nindicator("rollback scale")\nvar n=0\nn:=n+1\nplot(n)\nplot(ta.ema(close,20))\nplot(ta.sma(close,3))\nplot(close[1])'
    session=Session(profile_id=HISTORICAL_PROFILE_ID)
    capture=Capture();engine=ReplayEngine(session,session.compile(source,'rollback-scale.pine'),(PRIMARY,),'primary',capture)
    started=time.monotonic();ema=None
    for payload in events(bars):
        event=MarketEvent.from_payload(payload);engine.apply(event)
        if event.kind=='close':ema=event.close if ema is None else 2/21*event.close+(1-2/21)*ema
    values=[v['value'] for v in capture.values.values()]
    final_closes=[100.+i%7+.5 for i in range(bars-3,bars)]
    if values != [bars,ema,sum(final_closes)/3,final_closes[-2]]:raise AssertionError('Rollback changed independent confirmed expectations.')
    return {'bars':bars,'events':bars*3,'seconds':round(time.monotonic()-started,3),
        'confirmed_values':values,'runtime_checkpoint_bytes':len(canonical(engine.checkpoint()))}


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True);parser.add_argument('--bars',type=int,default=100000)
    args=parser.parse_args(argv)
    if not 1000<=args.bars<=1000000:parser.error('Use 1000..1000000 bars.')
    root=args.directory.resolve();root.mkdir(parents=True,exist_ok=False)
    report={'python':sys.version.split()[0],'bars':args.bars,'events':args.bars*3,
        'execution_sha256':implementation_hash(),'build':build_identity()}
    started=time.monotonic()
    with local_manager(root/'store') as manager:
        service=Replays(manager)
        config={'input_ref':manager.freeze_input({'source':SOURCE,'source_name':'replay-scale.pine'}),
            'contexts':[asdict(PRIMARY)],'primary_id':'primary','broker_settings':None,'timeout_seconds':3600}
        baseline=service.create(config)['replay_id'];resumable=service.create(config)['replay_id']
        batch=[]
        for event in events(args.bars):
            batch.append(event)
            if len(batch)==1000:
                service.append(baseline,batch);service.append(resumable,batch);batch=[]
        if batch:service.append(baseline,batch);service.append(resumable,batch)
        report['ingest_seconds']=round(time.monotonic()-started,3)
        print('Replay input accepted and archived.',flush=True)
        started=time.monotonic();expected=service.advance(baseline,checkpoint_every=30000)
        report['baseline_seconds']=round(time.monotonic()-started,3);expected_hashes=hashes(manager,expected)
        # Halfway through a bar after its next-open fills, before confirmation.
        cut=(args.bars//3)*3+1
        paused=service.advance(resumable,max_events=cut,checkpoint_every=30000)
        if paused['sequence']!=cut-1 or paused['contexts']['primary']['active'] is None:raise AssertionError('Missing midbar boundary.')
        report['pause_sequence']=paused['sequence'];report['runtime_checkpoint_bytes']=len(canonical(service.repository.checkpoint(resumable)['engine']))
        manager.services.lifecycle.collect(apply=True,grace_seconds=0)
        backup(manager.db,manager.directory,root/'backup')
    restore(root/'backup',root/'restored')
    # Kill an actual CLI coordinator while its replay child is working. The
    # child watches parent death; the next process resumes only published state.
    command=[sys.executable,'-X','utf8','-m','apps.openpine','replay','advance','--state-dir',str(root/'restored'),
        '--id',resumable,'--checkpoint-every','3000']
    with (root/'crash.log').open('wb') as log:
        child=subprocess.Popen(command,stdout=log,stderr=log,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        db=Database(root/'restored/catalog.sqlite3');repository=ReplayRepository(db,ObjectStore(root/'restored/objects'))
        try:
            deadline=time.monotonic()+120
            while time.monotonic()<deadline:
                state=repository.status(resumable)
                if state['sequence']>paused['sequence']:
                    child.kill();child.wait(timeout=10);break
                if child.poll() is not None:raise AssertionError('Coordinator completed before the intended crash boundary.')
                time.sleep(.02)
            else:raise AssertionError('No durable crash checkpoint.')
            report['coordinator_crash_sequence']=state['sequence']
        finally:
            db.close()
            if child.poll() is None:child.kill();child.wait(timeout=10)
    time.sleep(.3)
    started=time.monotonic()
    with local_manager(root/'restored') as manager:
        service=Replays(manager);actual=service.advance(resumable,checkpoint_every=30000)
        actual_hashes=hashes(manager,actual)
        if actual_hashes!=expected_hashes:raise AssertionError('Restored plot/ledger streams differ.')
        if actual['simulation']['statistics']!=expected['simulation']['statistics']:raise AssertionError('Restored broker accounting differs.')
        if actual['input_chain']!=expected['input_chain']:raise AssertionError('Restored input prefix differs.')
        manager.services.lifecycle.collect(apply=True,grace_seconds=0)
        if any(manager.directory.glob('replay-attempt-*')):raise AssertionError('Crashed replay work was not collected.')
        report.update(status='passed',hashes=actual_hashes,statistics=actual['simulation']['statistics'],
            resume_seconds=round(time.monotonic()-started,3),backup_restore=True,coordinator_crash=True,
            crashed_work_collected=True,replay_id=resumable,input_parts=len(manager.db.execute('SELECT object_id FROM replay_parts WHERE replay_id=?',(resumable,))))
    report['rollback']=rollback_proof(args.bars)
    atomic_json(root/'report.json',report);print(canonical(report).decode());return 0


if __name__=='__main__':raise SystemExit(main())
