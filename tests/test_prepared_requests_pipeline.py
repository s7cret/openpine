"""Frozen graph selection through actual workers, preparation, research and backup."""
import tempfile,unittest,uuid,time
from pathlib import Path
from dataclasses import asdict
from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.launches import Launches
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from apps.openpine.walkforward import WalkForwards
from packages.marketdata.providers import make_snapshot
from packages.storage.backup import backup,restore
from packages.contracts import OpenPineError
from tools.prepared_requests_spec import datasets,context,source
from test_environment_pipeline import wait

def prepare(manager,v=6):
    data=datasets();snapshots={k:manager.market.save(make_snapshot(d,{'provider':'uploaded-file.1'})) for k,d in data.items()}
    ref=manager.freeze_input({'source':source(v),'source_name':f'prepared-v{v}.pine'})
    catalog=[{'context':context(data[k]),'snapshot_id':snapshots[k]} for k in ('a1','a2','b1','b2')]
    return {'schema_version':'openpine.job-request.6','input_ref':ref,'snapshot_id':snapshots['parent'],'primary_context':context(data['parent']),
        'contexts':catalog,'inputs':{'sym':'TEST:B','tf':'1'},'timeout_seconds':120},snapshots

class PreparedRequestPipelineTests(unittest.TestCase):
    def test_const_alias_without_inputs_is_frozen_and_missing_data_rejects_before_job(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as m:
            raw,snapshots=prepare(m)
            text='//@version=6\nindicator("Constant target")\nsym="TEST:A"\ntf="2"\nplot(request.security(sym,tf,close))'
            ref=m.freeze_input({'source':text,'source_name':'constant-target.pine'})
            self.assertIn('artifact_ref',m.objects.get(ref));raw['input_ref']=ref;raw['inputs']={}
            with self.assertRaisesRegex(OpenPineError,'Missing prepared contexts'):m.submit({**raw,'contexts':[]})
            self.assertEqual(m.list(),[])
            key=m.submit(raw)['job_id'];wait(m,key)
            self.assertEqual(m.result(key)['result']['outputs'][0]['values'],[None,10.,10.,20.,20.,30.,30.,40.])
            self.assertEqual([b.snapshot_id for b in m.plan(key).contexts],[snapshots['a2']])

    def test_native_saved_ir_all_versions_choose_only_selected_graph_and_resume(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as m:
            for v in range(1,7):
                raw,snapshots=prepare(m,v);key=m.submit(raw,pause_after=3)['job_id'];wait(m,key,'paused')
                self.assertEqual([b.snapshot_id for b in m.plan(key).contexts],[snapshots['b1']])
                m.resume(key);wait(m,key)
                self.assertEqual(m.result(key)['result']['outputs'][0]['values'],[100.,101.,102.,103.,104.,105.,106.,107.])
    def test_requirements_and_provider_proposal_resolve_selected_values_before_io(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as m:
            raw,snapshots=prepare(m);h=HistoryJobs(m,start=False);service=Launches(m,h)
            try:
                selected=service.requirements(raw['input_ref'],raw['inputs'])
                self.assertEqual([(r['symbol'],r['timeframe']) for r in selected['contexts']],[('TEST:B','1')])
                config={'schema_version':'openpine.launch-request.1','request_id':uuid.uuid4().hex,'input_ref':raw['input_ref'],
                    'primary':{'kind':'snapshot','snapshot_id':raw['snapshot_id']},'contexts':[{'kind':'snapshot','snapshot_id':snapshots['b1']}],
                    'inputs':raw['inputs'],'timeout_seconds':120,'gap_policy':'fail'}
                state=service.create(config);self.assertEqual(state['status'],'ready',state)
                catalog=service.research_base(state['launch_id'],[snapshots['a2']]);self.assertEqual(len(catalog['contexts']),2)
                with self.assertRaises(ValueError):service.create({**config,'request_id':uuid.uuid4().hex,'inputs':{'tf':'0'}})
                self.assertEqual(h.repository.tasks(),[]);self.assertEqual(m.list(),[])
                key=service.start(state['launch_id'])['job_id'];wait(m,key)
                self.assertEqual(service.configuration(key)['context_graph']['sha256'],selected['context_graph']['sha256'])
            finally:h.close()
    def test_experiment_distinct_graphs_are_preflighted_before_any_job(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as m:
            raw,snapshots=prepare(m);service=Experiments(m,start=False)
            try:
                base={k:v for k,v in raw.items() if k!='schema_version'}
                base.update(name='Prepared variants',variants=[{'sym':'TEST:A','tf':'2'},{'sym':'TEST:B','tf':'1'}])
                made=service.create(base);key=made['experiment_id'];deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    service.tick();state=service.status(key)
                    if state['status']=='succeeded':break
                    if state['status']=='completed_with_errors':self.fail(state)
                    time.sleep(.03)
                self.assertEqual(state['status'],'succeeded')
                rows=service.repository.variants(key,start=0,limit=10)
                self.assertEqual([[b.snapshot_id for b in row['plan'].contexts] for row in rows],[[snapshots['a2']],[snapshots['b1']]])
                self.assertEqual([r['metrics']['outputs'][0]['last'] for r in state['variants']],[40.,107.])
                count=len(m.list())
                with self.assertRaisesRegex(OpenPineError,'Missing prepared contexts'):service.create({**base,'variants':[{'sym':'TEST:A','tf':'2'},{'sym':'TEST:MISSING','tf':'1'}]})
                self.assertEqual(len(m.list()),count)
            finally:service.close()
    def test_search_and_walkforward_keep_each_candidate_graph(self):
        with tempfile.TemporaryDirectory() as directory,local_manager(directory) as m:
            raw,snapshots=prepare(m);e=Experiments(m,start=False);s=Searches(m,e,start=False);wf=WalkForwards(m,s,start=False)
            try:
                base={k:v for k,v in raw.items() if k not in ('schema_version','inputs')}
                base.update(name='Prepared search',inputs={'tf':'2'},space={'sym':['TEST:A','TEST:B']},method='grid',budget=2,seed=0,
                    objective={'metric':'output_mean','output_index':0,'direction':'maximize'},
                    windows={'selection':{'warmup_start_ms':0,'score_start_ms':120000,'end_ms':240000},
                        'holdout':{'warmup_start_ms':0,'score_start_ms':240000,'end_ms':480000}})
                key=s.create(base)['search_id'];deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    s.tick();e.tick();state=s.status(key)
                    if state['status']=='succeeded':break
                    if state['status'] not in ('selecting','checking_holdout'):self.fail(state)
                    time.sleep(.03)
                self.assertEqual(state['status'],'succeeded');self.assertEqual(state['decision']['winner_ordinal'],1)
                request={k:v for k,v in base.items() if k!='windows'}
                request['walkforward']={'folds':1,'max_jobs':3,'mode':'rolling','start_ms':240000,'step_ms':120000,'test_ms':120000,'train_ms':120000,'warmup_ms':120000}
                key=wf.create(request)['walkforward_id'];deadline=time.monotonic()+120
                while time.monotonic()<deadline:
                    wf.tick();s.tick();e.tick();state=wf.status(key)
                    if state['status']=='succeeded':break
                    if state['status']=='completed_with_errors':self.fail(state)
                    time.sleep(.03)
                self.assertEqual(state['status'],'succeeded')
            finally:wf.close();s.close();e.close()

if __name__=='__main__':unittest.main()
