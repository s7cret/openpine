"""Real coordinator crash between completed, queued and unsubmitted variants."""
import argparse
import os
from pathlib import Path
import subprocess
import sys
import time
from apps.openpine.bootstrap import local_manager
from apps.openpine.experiments import Experiments
from packages.compiler.semantic.specification import ROOT
from packages.contracts.storage import ResourcePolicy
from packages.contracts.wire import atomic_json,read_json
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore


def child(root):
    with local_manager(root/'live',policy=ResourcePolicy(workers=1)) as manager:
        service=Experiments(manager,start=False);example=ROOT/'examples/inputs'
        ref=manager.freeze_input({'source':(example/'v6.pine').read_text(encoding='utf-8'),'source_name':'v6.pine'})
        snapshot=manager.market.save(input_snapshot(read_json(example/'bars.json')))
        group=service.create({'name':'Crash recovery','input_ref':ref,'snapshot_id':snapshot,'timeout_seconds':60,'grid':{'length':[2,3,4]}})
        identifier=group['experiment_id'];service.tick();first=service.status(identifier)['variants'][0]['job_id'];deadline=time.monotonic()+90
        while manager.get(first)['status'] not in ('succeeded','failed','timed_out','cancelled') and time.monotonic()<deadline:time.sleep(.02)
        if manager.get(first)['status']!='succeeded':raise AssertionError(manager.get(first))
        with manager.lock:
            service.tick();state=service.status(identifier)
            if [v['status'] for v in state['variants']]!=['succeeded','queued','planned']:raise AssertionError(state)
            atomic_json(root/'ready.json',{'experiment_id':identifier,'first':manager.get(first),'composition':service.repository.manifest(identifier)[1],
                'queued':state['variants'][1]['job_id'],'unsubmitted':state['variants'][2]['ordinal']})
            while True:time.sleep(.1)


def run(directory):
    root=Path(directory).resolve()
    if root.exists():raise ValueError('Use a new acceptance directory.')
    root.mkdir(parents=True);started=time.monotonic()
    with (root/'coordinator.log').open('wb') as log:
        process=subprocess.Popen([sys.executable,'-X','utf8','-m','tools.experiment_soak','--directory',str(root),'--child'],cwd=ROOT,
            stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        try:
            deadline=time.monotonic()+120
            while not (root/'ready.json').exists():
                if process.poll() is not None:raise AssertionError('Coordinator exited before cut: '+(root/'coordinator.log').read_text(encoding='utf-8',errors='replace')[-12000:])
                if time.monotonic()>deadline:raise TimeoutError('Experiment coordinator did not reach its durable cut.')
                time.sleep(.03)
            process.kill();process.wait(timeout=10)
        finally:
            if process.poll() is None:process.kill();process.wait(timeout=10)
    cut=read_json(root/'ready.json');identifier=cut['experiment_id']
    with local_manager(root/'live',policy=ResourcePolicy(workers=1)) as manager:
        with manager.lock:
            service=Experiments(manager,start=False);service.control(identifier,'pause')
            if service.repository.manifest(identifier)[1]!=cut['composition']:raise AssertionError('Composition changed after crash.')
            backup(manager.db,manager.directory,root/'backup');service.close()
    restore(root/'backup',root/'restored')
    with local_manager(root/'restored',policy=ResourcePolicy(workers=1)) as manager:
        service=Experiments(manager,start=False);service.control(identifier,'resume');deadline=time.monotonic()+120
        while time.monotonic()<deadline:
            service.tick();state=service.status(identifier)
            if state['status']!='running':break
            time.sleep(.04)
        if state['status']!='succeeded':raise AssertionError(state)
        first=manager.get(cut['first']['job_id'])
        if (first['result_id'],first['attempt_count'])!=(cut['first']['result_id'],cut['first']['attempt_count']):raise AssertionError('Completed variant was repeated.')
        if len(manager.list())!=3:raise AssertionError('Duplicated experiment jobs.')
        for variant,length in zip(state['variants'],(2,3,4),strict=True):
            values=manager.result(variant['job_id'])['result']['outputs'][0]['values']
            if values!=[length*1.5]*6:raise AssertionError('Restored variant changed values.')
        service.close()
    result={'status':'passed','python':sys.version.split()[0],'real_coordinator_kill':True,'cut':['succeeded','queued','planned'],
        'backup_restored':True,'completed_variant_repeated':False,'variant_jobs':3,'wall_seconds':round(time.monotonic()-started,3)}
    atomic_json(root/'report.json',result);return result


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--directory',required=True,type=Path);parser.add_argument('--child',action='store_true');args=parser.parse_args(argv)
    if args.child:child(args.directory)
    else:print(run(args.directory))
    return 0


if __name__=='__main__':raise SystemExit(main())
