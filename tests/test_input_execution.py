"""Configured native jobs, shared live sources and independent portable replay."""
from dataclasses import asdict
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from apps.openpine.bootstrap import local_manager
from apps.openpine.recordings import Recordings,open_recording
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.inputs import settings_for
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.realtime import ReplayEngine
from packages.execution.artifacts import import_program
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from test_inputs import code,DATA
from test_jobs import wait
from test_live_market import prepare,message
from test_realtime import PRIMARY,Sink,tape


def paused(manager,identifier):
    deadline=time.monotonic()+60
    while time.monotonic()<deadline:
        state=manager.get(identifier)
        if state['status']=='paused':return state
        if state['status'] in ('failed','cancelled','timed_out','succeeded'):raise AssertionError(state)
        time.sleep(.03)
    raise AssertionError(manager.get(identifier))


def drain(live,history,identifiers):
    deadline=time.monotonic()+90
    while time.monotonic()<deadline:
        for identifier in identifiers:live.tick(identifier,execute=False)
        for task in history.repository.tasks():
            if task['status']=='queued':history.process(task['task_id'])
        live._dispatch()
        states=[live.status(i) for i in identifiers]
        if any(s['status'] in ('failed','execution_failed') for s in states):raise AssertionError(states)
        if all(s['warmup_done'] and s['unprojected_records']==0 and s['replay']['pending_events']==0 for s in states):return states
    raise AssertionError(states)


class InputExecutionTests(unittest.TestCase):
    def test_configured_strategy_and_requested_contexts_keep_joint_result(self):
        from packages.compiler.semantic.specification import ROOT
        from packages.marketdata import read_bars
        from apps.openpine.contexts import load_context_inputs
        from tools.conformance import context_inputs
        example=ROOT/'examples/strategies';session=Session(profile_id=HISTORICAL_PROFILE_ID)
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
            snapshot=manager.market.save(input_snapshot(loads((example/'wave.json').read_bytes())))
            for version in range(1,7):
                source=(example/f'v{version}-filtered.pine').read_text(encoding='utf-8')
                source=source.replace('higher=',('size=input.int(1)' if version>=5 else 'size=input(1)')+'\nhigher=',1)
                source=source.replace('strategy.long)','strategy.long,qty=size)').replace('"L",true)','"L",true,qty=size)')
                program=session.compile(source,f'configured-v{version}.pine')
                expected=session.execute(program,data=read_bars(example/'wave.json'),inputs={'size':2},**context_inputs(example/'inputs.json'))
                self.assertIsNotNone(expected.simulation);self.assertEqual(len(expected.contexts),1)
                self.assertEqual(expected.primary_context.context_id,'strategy.primary')
                self.assertEqual(expected.settings['values']['input:size'],2)
                if version not in (1,6):continue
                ref=manager.freeze_input({'source':source,'source_name':f'configured-v{version}.pine'})
                job=manager.submit({'schema_version':'openpine.job-request.6','input_ref':ref,'snapshot_id':snapshot,'timeout_seconds':60,'inputs':{'size':2},**load_context_inputs(manager,example/'inputs.json',None)})['job_id']
                self.assertEqual(wait(manager,job)['status'],'succeeded',manager.get(job))
                self.assertEqual(loads(canonical(manager.result(job)['result'])),loads(canonical(asdict(expected))))

    def test_native_all_versions_reuse_ir_and_paused_backup_pins_settings(self):
        session=Session(profile_id=HISTORICAL_PROFILE_ID)
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);expected={}
            with local_manager(root/'live') as manager:
                snapshot=manager.market.save(input_snapshot(loads(canonical(asdict(DATA)))))
                for version in range(1,7):
                    ref=manager.freeze_input({'source':code(version),'source_name':f'input-v{version}.pine'})
                    program=import_program(manager.objects.get(manager.objects.get(ref)['artifact_ref']))
                    values={'length':3,'scale':2,'src':'open'}
                    request={'schema_version':'openpine.job-request.6','input_ref':ref,'snapshot_id':snapshot.snapshot_id if hasattr(snapshot,'snapshot_id') else snapshot,
                        'timeout_seconds':60,'inputs':values}
                    with patch('packages.execution.session.compile_source',side_effect=AssertionError('Variants recompiled the source.')):
                        job=manager.submit(request,pause_after=3 if version==6 else None,checkpoint_interval_bars=1)['job_id']
                    expected[job]=loads(canonical(asdict(session.execute(program,data=DATA,inputs=values))))
                    self.assertEqual(manager.plan(job).schema_version,'openpine.run-plan.5')
                    self.assertEqual(manager.request(job)['settings'],settings_for(program,values))
                    if version==6:
                        paused(manager,job);selected=job
                    else:
                        self.assertEqual(wait(manager,job)['status'],'succeeded',manager.get(job))
                        actual=loads(canonical(manager.result(job)['result']))
                        for key,value in expected[job].items():self.assertEqual(actual.get(key),value,key)
                first_plan=manager.plan(selected).payload();backup(manager.db,manager.directory,root/'backup')
            restore(root/'backup',root/'restored')
            with local_manager(root/'restored') as manager:
                self.assertEqual(manager.plan(selected).payload(),first_plan)
                manager.resume(selected);self.assertEqual(wait(manager,selected)['status'],'succeeded',manager.get(selected))
                self.assertEqual(loads(canonical(manager.result(selected)['result'])),expected[selected])

    def test_shared_live_feed_keeps_variants_independent_and_recordings_replay_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);exports=[]
            with local_manager(root/'live') as manager:
                source='//@version=6\nindicator("configured live")\ngain=input.int(2)\nplot(close*gain)'
                history,live,first=prepare(manager,source=source);config=live.repository.session_config(first)
                payload={k:config[k] for k in ('input_ref','instrument','timeframe','start_ms','broker_settings','timeout_seconds')}
                second=live.create({**payload,'inputs':{'gain':3}},connect=False)['session_id']
                states=drain(live,history,[first,second]);self.assertEqual(states[0]['feed_id'],states[1]['feed_id'])
                live.hub.ingest(states[0]['feed_id'],message(),received_at=400001)
                live.hub.ingest(states[0]['feed_id'],message(closed=True,volume='101'),received_at=400002)
                states=drain(live,history,[first,second])
                for identifier,gain,state in zip((first,second),(2,3),states,strict=True):
                    self.assertEqual(live.view(identifier,'chart',0,100)['rows'][-1][-1],10.*gain)
                    self.assertEqual(state['replay']['settings']['values']['input:gain'],gain)
                    live.stop(identifier);bundle=root/f'recording-{gain}';Recordings(manager).export(identifier,bundle);exports.append(bundle)
                    self.assertEqual(open_recording(bundle)[0]['schema_version'],'openpine.session-recording.2')
                live.close();history.close()
            for index,bundle in enumerate(exports):
                with local_manager(root/f'offline-{index}') as manager:self.assertEqual(Recordings(manager).replay(bundle)['status'],'passed')

    def test_replay_identity_and_checkpoint_bind_settings_in_all_versions(self):
        session=Session(profile_id=HISTORICAL_PROFILE_ID)
        for version in range(1,7):
            program=session.compile(code(version));settings=settings_for(program,{'scale':2})
            original=ReplayEngine(session,program,(PRIMARY,),'primary',Sink(),settings=settings)
            events=list(tape())
            for event in events[:5]:original.apply(event)
            saved=original.checkpoint();other=ReplayEngine(session,program,(PRIMARY,),'primary',Sink(),settings=settings_for(program,{'scale':3}))
            with self.assertRaises(ValueError):other.restore(saved)
            self.assertEqual(other.evaluation.count,0)
            restored=ReplayEngine(session,program,(PRIMARY,),'primary',Sink(),settings=settings);restored.restore(saved)
            for event in events[5:]:original.apply(event);restored.apply(event)
            self.assertEqual(original.status(),restored.status())


if __name__=='__main__':unittest.main()
