"""Subprocess probe used against independently installed previous/current builds."""
import argparse
import json
import sys
import time
from pathlib import Path


def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=('seed','continue','incompatible'))
    p.add_argument('--root',type=Path,required=True);p.add_argument('--store',type=Path,required=True);p.add_argument('--receipt',type=Path,required=True)
    args=p.parse_args(argv);sys.path[:0]=[str(args.root),str(args.root/'tests')]
    from apps.openpine.bootstrap import local_manager
    from apps.openpine.experiments import Experiments
    from apps.openpine.replay import Replays
    from packages.contracts.wire import atomic_json, read_json
    from packages.execution.identity import build_identity, implementation_hash
    from test_jobs import request, wait
    from test_realtime import configuration, text, tape
    from test_experiments import prepare, complete
    identity={'build':build_identity(),'implementation_sha256':implementation_hash()}
    if args.mode=='seed':
        with local_manager(args.store) as manager:
            ready=manager.submit(request(1))['job_id'];assert wait(manager,ready)['status']=='succeeded'
            paused=manager.submit(request(6),checkpoint_interval_bars=1,pause_after=2)['job_id']
            deadline=time.monotonic()+90
            while manager.get(paused)['status']!='paused':
                assert time.monotonic()<deadline,manager.get(paused);time.sleep(.05)
            blocked=manager.submit(request(6),checkpoint_interval_bars=1,pause_after=2)['job_id']
            deadline=time.monotonic()+90
            while manager.get(blocked)['status']!='paused':
                assert time.monotonic()<deadline,manager.get(blocked);time.sleep(.05)
            replays=Replays(manager);replay=replays.create(configuration(manager,text()))['replay_id']
            replays.append(replay,[e.payload() for e in tape()][:6]);replays.advance(replay,max_events=6)
            research=Experiments(manager,start=False)
            try:
                experiment=research.create({**prepare(manager),'variants':[{'length':2},{'length':3}]})['experiment_id']
                research.control(experiment,'pause')
            finally:research.close()
            value={'ready':ready,'paused':paused,'blocked':blocked,'blocked_checkpoint':manager.get(blocked)['checkpoint_id'],'replay':replay,'experiment':experiment,
                   'snapshot':manager.request(paused)['snapshot_id'],'old_plan':manager.plan(paused).payload(),
                   'ready_result':manager.get(ready)['result_id'],'replay_checkpoint':replays.status(replay)['checkpoint_ref'],
                   'schema':manager.db.connection.pragma('user_version'),'identity':identity}
        atomic_json(args.receipt,value);print(json.dumps({'seeded':True,**identity}));return
    saved=read_json(args.receipt)
    from apps.openpine.diagnostics import compatibility, doctor
    if args.mode=='continue':
        assert identity['implementation_sha256']==saved['identity']['implementation_sha256']
        assert identity['build']!=saved['identity']['build']
        assert doctor(args.store,full=True)['status']=='ok'
        for kind in ('jobs','programs','replays','research'):
            rows=compatibility(args.store,kind=kind)['items'];assert rows,(kind,'empty')
            assert all(r['status'] in ('compatible','compile_on_run') for r in rows),(kind,rows)
        with local_manager(args.store) as manager:
            assert manager.get(saved['ready'])['result_id']==saved['ready_result']
            assert manager.result(saved['ready'])['result']['outputs'][0]['values'][-1]==135.
            assert manager.plan(saved['paused']).payload()==saved['old_plan']
            manager.resume(saved['paused']);state=wait(manager,saved['paused']);assert state['status']=='succeeded',state
            assert state['resumed_from_bar']==2
            replays=Replays(manager);assert replays.status(saved['replay'])['checkpoint_ref']==saved['replay_checkpoint']
            replays.append(saved['replay'],[e.payload() for e in tape()][6:]);replays.advance(saved['replay'],max_events=6)
            assert replays.status(saved['replay'])['confirmed_bars']==4
            research=Experiments(manager,start=False)
            try:
                research.control(saved['experiment'],'resume');result=complete(research,saved['experiment'])
                assert result['status']=='succeeded',result
            finally:research.close()
        value={'status':'passed','compatible_build_change':True,'resumed_from_bar':2,'replay_bars':4,'research_variants':2,
               'old_result_retained':True,'identity':identity}
    else:
        assert identity['implementation_sha256']!=saved['identity']['implementation_sha256']
        rows=compatibility(args.store)['items'];assert all(r['status']=='recompile_required' for r in rows),rows
        from apps.openpine.restarts import preview, apply
        with local_manager(args.store) as manager:
            manager.resume(saved['blocked']);refused=wait(manager,saved['blocked'])
            assert refused['status']=='failed' and refused['error']['code']=='PLAN_IMPLEMENTATION',refused
            assert manager.get(saved['blocked'])['checkpoint_id']==saved['blocked_checkpoint']
        with local_manager(args.store,autostart=False) as manager:
            old_result=manager.get(saved['ready'])['result_id']
            report=preview(manager,saved['blocked']);assert any(c['field']=='execution_sha256' for c in report['changes'])
            receipt=apply(manager,saved['blocked'],preview_id=report['preview_id'],request_id='e'*32)
            assert manager.get(saved['ready'])['result_id']==old_result
            assert manager.request(receipt['job_id'])['snapshot_id']==saved['snapshot']
            assert manager.get(receipt['job_id'])['checkpoint_id'] is None
        with local_manager(args.store) as manager:
            state=wait(manager,receipt['job_id']);assert state['status']=='succeeded',state
            assert manager.result(receipt['job_id'])['result']['outputs'][0]['values'][-1]==135.
        value={'status':'passed','incompatible_compute_change':True,'old_resume_rejected':refused['error']['code'],'explicit_recompile':True,'new_job_id':receipt['job_id'],
               'old_result_retained':True,'checkpoint_reused':False,'identity':identity}
    atomic_json(args.receipt.with_name(args.mode+'.json'),value);print(json.dumps(value))


if __name__=='__main__':main()
