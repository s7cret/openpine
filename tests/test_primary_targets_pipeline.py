"""Metadata-only planning, real primary workers, research and portable live state."""
import tempfile,time,unittest,uuid
from pathlib import Path
from dataclasses import asdict
from unittest.mock import patch
from apps.openpine.bootstrap import local_manager
from apps.openpine.history_service import HistoryJobs
from apps.openpine.launches import Launches
from apps.openpine.experiments import Experiments
from apps.openpine.optimization import Searches
from apps.openpine.walkforward import WalkForwards
from packages.contracts import OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.wire import canonical
from packages.marketdata.providers import make_snapshot
from tools.primary_targets_spec import source
from tools.prepared_requests_spec import datasets,context
from test_environment_pipeline import wait
import test_prepared_requests_live as live
original_live=live.code

def prepare(m,v=6,mode='inputs'):
    data=datasets();snapshots={n:m.market.save(make_snapshot(d,{'provider':'uploaded-file.1'})) for n,d in data.items()}
    ref=m.freeze_input({'source':source(v,mode),'source_name':f'primary-v{v}.pine'})
    raw={'schema_version':'openpine.job-request.6','input_ref':ref,'snapshot_id':snapshots['b1'],'inputs':{},'timeout_seconds':120,
        'contexts':[{'context':context(data[k]),'snapshot_id':snapshots[k]} for k in ('a2','b2')], 'primary_context':context(data['b1'])}
    return raw,snapshots

def finish_services(target,key,*services):
    deadline=time.monotonic()+180
    while time.monotonic()<deadline:
        for service in services:service.tick()
        row=target.status(key)
        if row['status']=='succeeded':return row
        if row['status'] in ('completed_with_errors','holdout_failed','no_eligible_candidate'):raise AssertionError(row)
        time.sleep(.03)
    raise TimeoutError(row)

class PrimaryTargetPipelineTests(unittest.TestCase):
    def test_requirements_resolve_from_metadata_without_candle_reads_or_history_tasks(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as m:
            raw,snapshots=prepare(m);history=HistoryJobs(m,start=False);service=Launches(m,history)
            try:
                pending=service.requirements(raw['input_ref']);self.assertEqual(pending['status'],'needs_primary')
                self.assertEqual(pending['required_primary_fields'],['symbol','timeframe']);self.assertEqual(pending['contexts'],[])
                with patch.object(m.market,'source',side_effect=AssertionError('Planning read candles')):
                    ready=service.requirements(raw['input_ref'],primary_context=raw['primary_context'])
                self.assertEqual(ready['status'],'resolved');self.assertEqual([(r['symbol'],r['timeframe']) for r in ready['contexts']],[('TEST:B','1')])
                self.assertEqual(history.repository.tasks(),[]);self.assertEqual(m.list(),[])
            finally:history.close()
    def test_one_saved_ir_uses_three_primary_markets_and_foreign_metadata_fails_before_queue(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as m:
            for v in range(1,7):
                raw,snapshots=prepare(m,v,'references');original=canonical(m.objects.get(raw['input_ref']))
                for name in ('a1','b1','b2'):
                    request={**raw,'snapshot_id':snapshots[name]};request.pop('primary_context');request.pop('contexts')
                    key=m.submit(request,pause_after=1)['job_id'];wait(m,key,'paused');m.resume(key);wait(m,key)
                    self.assertEqual(m.plan(key).contexts,());self.assertEqual(m.result(key)['result']['outputs'][0]['values'],[bar.close for bar in datasets()[name].bars])
                    cfg=Launches(m,None).configuration(key);self.assertEqual((cfg['context_graph']['primary']['symbol'],cfg['context_graph']['primary']['timeframe']),(datasets()[name].symbol,datasets()[name].timeframe))
                count=len(m.list())
                with self.assertRaisesRegex(OpenPineError,'metadata/source differs|Primary metadata differs'):m.submit({**raw,'primary_context':context(datasets()['a1'])})
                self.assertEqual(len(m.list()),count);self.assertEqual(canonical(m.objects.get(raw['input_ref'])),original)
    def test_experiment_and_search_keep_raw_inheritance_and_resolved_candidate_graphs(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as m:
            raw,snapshots=prepare(m);e=Experiments(m,start=False);s=Searches(m,e,start=False);wf=WalkForwards(m,s,start=False)
            try:
                base={k:v for k,v in raw.items() if k not in ('schema_version','inputs')}
                key=e.create({**base,'name':'Primary variants','variants':[{'sym':'','tf':''},{'sym':'TEST:A','tf':'2'}]})['experiment_id']
                state=finish_services(e,key,e);rows=e.repository.variants(key,start=0,limit=10)
                self.assertEqual([[b.snapshot_id for b in row['plan'].contexts] for row in rows],[[],[snapshots['a2']]])
                self.assertEqual([r['metrics']['outputs'][0]['last'] for r in state['variants']],[107.,40.])
                search={**base,'name':'Primary search','inputs':{'sym':'','tf':'2'},'space':{'sym':['','TEST:A']},'method':'grid','budget':2,'seed':0,
                    'objective':{'metric':'output_mean','output_index':0,'direction':'maximize'},
                    'windows':{'selection':{'warmup_start_ms':0,'score_start_ms':120000,'end_ms':240000},'holdout':{'warmup_start_ms':0,'score_start_ms':240000,'end_ms':480000}}}
                sk=s.create(search)['search_id'];state=finish_services(s,sk,s,e);self.assertEqual(state['decision']['winner_ordinal'],0)
                request={k:v for k,v in search.items() if k!='windows'};request['walkforward']={'folds':1,'max_jobs':3,'mode':'rolling','start_ms':240000,'step_ms':120000,'test_ms':120000,'train_ms':120000,'warmup_ms':120000}
                wk=wf.create(request)['walkforward_id'];finish_services(wf,wk,wf,s,e)
            finally:wf.close();s.close();e.close()
    def test_invalid_primary_direction_fails_before_any_acquisition(self):
        with tempfile.TemporaryDirectory() as d,local_manager(d) as m:
            raw,snapshots=prepare(m);h=HistoryJobs(m,start=False);service=Launches(m,h)
            try:
                with self.assertRaises(ValueError):service.requirements(raw['input_ref'],{'tf':'1'},primary_context=context(datasets()['b2']))
                self.assertEqual(m.list(),[]);self.assertEqual(h.repository.tasks(),[])
            finally:h.close()

def relative_live(v):
    api='security' if v<5 else 'request.security';symbol='tickerid' if v<4 else 'syminfo.tickerid'
    return original_live(v).replace(f'{api}(sym,tf,',f'{api}({symbol},tf,')

class PrimaryTargetLiveTests(live.PreparedRequestLiveTests):
    def setUp(self):
        guard=patch.object(live,'code',relative_live);guard.start();self.addCleanup(guard.stop)

if __name__=='__main__':unittest.main()
