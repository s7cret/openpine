"""Real coordinator death before holdout admission or between completed folds."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from apps.openpine.walkforward import WalkForwards
from packages.compiler.semantic.specification import ROOT
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import read_json, atomic_json
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup, restore


def services(manager):
    experiments=Experiments(manager,start=False)
    searches=Searches(manager,experiments,start=False)
    return WalkForwards(manager,searches,start=False), searches, experiments


def child(root, cut):
    with local_manager(root/'live',policy=ResourcePolicy(workers=1)) as manager:
        studies,searches,experiments=services(manager)
        payload=read_json(ROOT/'examples/search/request.json');payload.pop('windows')
        payload['input_ref']=manager.freeze_input({'source':(ROOT/'examples/search/v6.pine').read_text(encoding='utf-8'),'source_name':'v6.pine'})
        payload['snapshot_id']=manager.market.save(input_snapshot(read_json(ROOT/'examples/inputs/bars.json')))
        payload['walkforward']={'mode':'rolling','start_ms':60000,'train_ms':120000,'test_ms':60000,
                               'step_ms':60000,'warmup_ms':60000,'folds':2,'max_jobs':8}
        identifier=studies.create(payload)['walkforward_id']
        deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            studies.tick();searches.tick()
            state=studies.status(identifier)
            first=searches.status(state['folds'][0]['search_id'])
            ready=(state['completed']==1) if cut=='fold' else first['decision'] is not None and first['holdout'] is None
            if ready:
                with manager.lock:
                    completed={r['job_id']:{'result_id':r['result_id'],'attempt_count':r['attempt_count']}
                               for r in manager.list() if r['status']=='succeeded'}
                    atomic_json(root/'ready.json',{'walkforward_id':identifier,'manifest_sha256':state['manifest_sha256'],
                        'completed':completed,'first_decision':first['decision'],'receipt':state['folds'][0]['receipt']})
                    while True:time.sleep(.1)
            experiments.tick();time.sleep(.03)
        raise TimeoutError('Study did not reach its durable cut.')


def run(directory, cut='fold'):
    root=Path(directory).resolve()
    if root.exists():raise ValueError('Use a new proof directory.')
    root.mkdir(parents=True)
    with (root/'coordinator.log').open('wb') as log:
        process=subprocess.Popen([sys.executable,'-X','utf8','-m','tools.walkforward_soak','--directory',str(root),'--cut',cut,'--child'],
            cwd=ROOT,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+210
            while not (root/'ready.json').exists():
                if process.poll() is not None:raise AssertionError((root/'coordinator.log').read_text(encoding='utf-8',errors='replace')[-12000:])
                if time.monotonic()>deadline:raise TimeoutError('Coordinator did not reach a cut.')
                time.sleep(.04)
            process.kill();process.wait(timeout=10)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
    saved=read_json(root/'ready.json');identifier=saved['walkforward_id']
    with local_manager(root/'live',policy=ResourcePolicy(workers=1)) as manager:
        studies,searches,experiments=services(manager)
        studies.control(identifier,'pause')
        backup(manager.db,manager.directory,root/'backup')
        studies.close();searches.close();experiments.close()
    restore(root/'backup',root/'restored')
    with local_manager(root/'restored',policy=ResourcePolicy(workers=1)) as manager:
        studies,searches,experiments=services(manager)
        studies.control(identifier,'resume');deadline=time.monotonic()+180
        while time.monotonic()<deadline:
            studies.tick();searches.tick();experiments.tick();state=studies.status(identifier)
            if state['status']!='running':break
            time.sleep(.04)
        if state['status']!='succeeded':raise AssertionError(state)
        if state['manifest_sha256']!=saved['manifest_sha256']:raise AssertionError('Study was replanned.')
        first=searches.status(state['folds'][0]['search_id'])
        if first['decision']!=saved['first_decision']:raise AssertionError('Selection decision changed after crash.')
        if saved['receipt'] is not None and state['folds'][0]['receipt']!=saved['receipt']:raise AssertionError('Completed receipt changed.')
        for job,expected in saved['completed'].items():
            actual=manager.get(job)
            if any(actual[k]!=value for k,value in expected.items()):raise AssertionError('Completed candidate was repeated.')
        scores=[f['receipt']['holdout']['assessment']['score'] for f in state['folds']]
        if scores!=['39.0','42.0'] or len(manager.list())!=8:raise AssertionError((scores,len(manager.list())))
        manager.db.check();studies.close();searches.close();experiments.close()
    result={'status':'passed','python':sys.version.split()[0],'cut':cut,'real_coordinator_kill':True,
            'backup_restored':True,'completed_candidate_repeated':False,'decision_preserved':True,'jobs':8,'holdout_scores':scores}
    atomic_json(root/'report.json',result)
    return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory',type=Path,required=True)
    parser.add_argument('--cut',choices=('fold','decision'),default='fold')
    parser.add_argument('--child',action='store_true');args=parser.parse_args(argv)
    if args.child:child(args.directory,args.cut)
    else:print(run(args.directory,args.cut))
    return 0


if __name__=='__main__':raise SystemExit(main())
