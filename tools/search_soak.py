"""Kill a real coordinator during selection or after the atomic holdout decision."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from packages.compiler.semantic.specification import ROOT
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json,read_json
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore


def child(root,cut):
    with local_manager(root/'live',policy=ResourcePolicy(workers=1)) as manager:
        experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False)
        example=ROOT/'examples/search';payload=read_json(example/'request.json')
        payload['input_ref']=manager.freeze_input({'source':(example/'v6.pine').read_text(encoding='utf-8'),'source_name':'v6.pine'})
        payload['snapshot_id']=manager.market.save(input_snapshot(read_json(ROOT/'examples/inputs/bars.json')))
        identifier=service.create(payload)['search_id'];experiments.tick();first=service.status(identifier)['variants'][0]['job_id']
        deadline=time.monotonic()+150
        while time.monotonic()<deadline:
            if cut=='selection':
                if manager.get(first)['status']=='succeeded':
                    service.tick()
                    with manager.lock:
                        experiments.tick();state=service.status(identifier)
                        if [r['status'] for r in state['variants']]!=['succeeded','queued','planned']:raise AssertionError(state)
                        atomic_json(root/'ready.json',{'search_id':identifier,'first':manager.get(first),'manifest_sha256':state['manifest_sha256'],
                            'decision':state['decision'],'cut':['succeeded','queued','planned']})
                        while True:time.sleep(.1)
            else:
                service.tick();state=service.status(identifier)
                if state['decision'] is not None:
                    with manager.lock:
                        saved=service.repository.get(identifier)
                        if state['decision']['winner_ordinal']!=2 or experiments.repository.pending(saved['holdout_id']) is None:
                            raise AssertionError('Holdout was not durably planned before execution.')
                        atomic_json(root/'ready.json',{'search_id':identifier,'first':manager.get(first),'manifest_sha256':state['manifest_sha256'],
                            'decision':state['decision'],'holdout_id':saved['holdout_id'],'cut':'decision_committed_holdout_unsubmitted'})
                        while True:time.sleep(.1)
                experiments.tick()
            time.sleep(.03)
        raise TimeoutError('Coordinator did not reach the requested search cut.')


def run(directory,cut='selection'):
    root=Path(directory).resolve()
    if root.exists():raise ValueError('Use a new proof directory.')
    root.mkdir(parents=True);started=time.monotonic()
    with (root/'coordinator.log').open('wb') as log:
        process=subprocess.Popen([sys.executable,'-X','utf8','-m','tools.search_soak','--directory',str(root),'--cut',cut,'--child'],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+180
            while not (root/'ready.json').exists():
                if process.poll() is not None:raise AssertionError((root/'coordinator.log').read_text(encoding='utf-8',errors='replace')[-10000:])
                if time.monotonic()>deadline:raise TimeoutError('Search coordinator did not reach its durable cut.')
                time.sleep(.04)
            process.kill();process.wait(timeout=10)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
    saved=read_json(root/'ready.json');identifier=saved['search_id']
    with local_manager(root/'live',policy=ResourcePolicy(workers=1)) as manager:
        experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False)
        service.control(identifier,'pause');backup(manager.db,manager.directory,root/'backup');service.close();experiments.close()
    restore(root/'backup',root/'restored')
    with local_manager(root/'restored',policy=ResourcePolicy(workers=1)) as manager:
        experiments=Experiments(manager,start=False);service=Searches(manager,experiments,start=False)
        service.control(identifier,'resume');deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            service.tick();experiments.tick();state=service.status(identifier)
            if state['status'] not in ('selecting','checking_holdout'):break
            time.sleep(.04)
        if state['status']!='succeeded':raise AssertionError(state)
        first=manager.get(saved['first']['job_id'])
        if (first['result_id'],first['attempt_count'])!=(saved['first']['result_id'],saved['first']['attempt_count']):raise AssertionError('Completed candidate was repeated.')
        if state['manifest_sha256']!=saved['manifest_sha256']:raise AssertionError('Search was replanned after restart.')
        if saved['decision'] is not None and state['decision']!=saved['decision']:raise AssertionError('Selection decision changed after crash.')
        if cut=='holdout' and service.repository.get(identifier)['holdout_id']!=saved['holdout_id']:raise AssertionError('Duplicate holdout experiment.')
        if len(manager.list())!=4:raise AssertionError('Expected exactly three candidate jobs and one holdout.')
        if [r['assessment']['score'] for r in state['variants']]!=['12.0','24.0','36.0'] or state['holdout']['assessment']['score']!='43.5':
            raise AssertionError('Recovered scores differ from manual expectations.')
        manager.db.check();service.close();experiments.close()
    result={'status':'passed','python':sys.version.split()[0],'cut':saved['cut'],'real_coordinator_kill':True,
        'backup_restored':True,'completed_candidate_repeated':False,'jobs':4,'decision_preserved':True,
        'wall_seconds':round(time.monotonic()-started,3)}
    atomic_json(root/'report.json',result);return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',required=True,type=Path)
    parser.add_argument('--cut',choices=('selection','holdout'),default='selection');parser.add_argument('--child',action='store_true');args=parser.parse_args(argv)
    if args.child:child(args.directory,args.cut)
    else:print(run(args.directory,args.cut))
    return 0


if __name__=='__main__':raise SystemExit(main())
