"""Risk rules through managed workers, contexts, backup and exact replay boundaries."""
import copy,shutil,tempfile,unittest
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.economics import EconomicsSettings
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.environment import source_policy
from packages.marketdata import from_payload,data_identity
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from tools.risk_fixtures import source,bars,SETTINGS,prepare,FAMILIES
from tools.risk_oracle import expected,DAY
from tools.specification import ROOT,read_json
import test_environment_pipeline as environment

def request(manager,v=6,family='daily',manual=0):
 root=ROOT/'examples/risk'
 return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':(root/f'{family}-v{v}.pine').read_text(),'source_name':f'{family}-v{v}.pine'}),
  'snapshot_id':manager.market.save(input_snapshot(read_json(root/f'{family}-input.json'))),'inputs':{'manual':float(manual)},'timeout_seconds':180}

def normalized(stats):
 stats=copy.deepcopy(stats)
 if stats['risk']['halt']:stats['risk']['halt'].pop('call_site_id')
 return stats

class RiskPipelineTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
 def test_six_versions_every_bar_boundary_and_both_overrides_restore_exactly(self):
  for v in range(1,7):
   for f in FAMILIES:
    for manual in (0,1):
     p=self.session.compile(source(v,f,manual='1' if manual else 'na'));runtime=self.session.validate_program(p);market=from_payload(bars(f,3))
     policy=source_policy(p,MemorySource(market),EconomicsSettings.from_payload(SETTINGS));baseline=self.session.execute(p,data=market,broker_settings=policy.settings)
     for cut in range(1,25):
      def driver():return HistoricalDriver(runtime.prepare(p),MemorySource(market),simulation_policy=policy)
      first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))));second=driver();second.restore(saved[0]);second.run()
      self.assertEqual(second.finish(data_identity(market)),baseline,(v,f,manual,cut))
 def test_eighteen_workers_freeze_rules_pause_reopen_and_expose_risk_state(self):
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   for v in range(1,7):
    for f in FAMILIES:
     with self.subTest(v=v,f=f):
      cut={'entry':4,'daily':2,'lifetime':10}[f];key=manager.submit(request(manager,v,f),pause_after=cut)['job_id'];environment.wait(manager,key,'paused');manager.resume(key);state=environment.wait(manager,key)
      r=manager.result(key)['result'];values,stats=expected(f);self.assertEqual(tuple(tuple(o['values']) for o in r['outputs']),values);self.assertEqual(normalized(r['simulation']['statistics']),stats)
      self.assertEqual(state['resumed_from_bar'],cut);self.assertEqual(manager.plan(key).simulation_policy.timeframe,'180')
      self.assertEqual(manager.plan(key).simulation_policy.profile_id,'openpine.risk-broker.1');self.assertEqual(manager.plan(key).simulation_policy.settings,EconomicsSettings.from_payload(SETTINGS))
      chart=manager.chart(key,start=0,limit=24);self.assertEqual(chart['schema_version'],'openpine.chart-window.9');self.assertLessEqual(chart['protection']['event_bytes'],chart['protection']['event_byte_budget'])
      events=chart['protection']['events'];self.assertTrue(all('risk' in e['value'] for e in events if e['kind']=='mark'))
      kinds={e['kind'] for e in events};self.assertTrue({'quantity_resolved','commission_calculated','risk_period'}<=kinds)
      self.assertIn('risk_adjusted' if f=='entry' else 'risk_triggered',kinds)
      if f=='daily':
       mark=next(e['value']['risk'] for e in events if e['kind']=='mark' and e['bar_index']==1);self.assertTrue(mark['needs_close']);self.assertTrue(mark['blocked'])
       self.assertEqual(next(e['bar_index'] for e in events if e['kind']=='risk_liquidation'),2)
 def test_backup_preserves_deferred_liquidation_and_cumulative_fees(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   with local_manager(root/'live') as manager:
    key=manager.submit(request(manager),pause_after=2)['job_id'];environment.wait(manager,key,'paused');backup(manager.db,manager.directory,root/'backup')
   restore(root/'backup',root/'restored')
   with local_manager(root/'restored') as manager:
    manager.resume(key);state=environment.wait(manager,key);self.assertEqual(state['resumed_from_bar'],2)
    result=manager.result(key)['result'];values,stats=expected('daily');self.assertEqual(tuple(tuple(o['values']) for o in result['outputs']),values);self.assertEqual(normalized(result['simulation']['statistics']),stats)
 def test_global_halt_survives_reopening_coordinator_and_manual_override(self):
  with tempfile.TemporaryDirectory() as d:
   with local_manager(d) as manager:
    key=manager.submit(request(manager,family='lifetime',manual=1),pause_after=17)['job_id'];environment.wait(manager,key,'paused')
   with local_manager(d) as manager:
    manager.resume(key);environment.wait(manager,key);result=manager.result(key)['result'];values,stats=expected('lifetime',1)
    self.assertEqual(tuple(tuple(o['values']) for o in result['outputs']),values);self.assertEqual(normalized(result['simulation']['statistics']),stats)
 def test_snapshot_rule_conflict_fails_before_queue_and_risk_calendar_is_pinned(self):
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   payload=request(manager);payload['broker_settings']={**SETTINGS,'minimum_notional':'100'}
   with self.assertRaises(OpenPineError):manager.submit(payload)
 def test_risk_declarations_are_not_copied_into_private_requested_contexts(self):
  from packages.contracts.contexts import MarketContext,ContextBinding
  from packages.marketdata.providers import make_snapshot
  primary=from_payload(bars('entry',3));higher=from_payload({'schema_version':'openpine.historical-bars.2','context_id':'risk.daily','symbol':'TEST:RISK','timeframe':'D','bars':[{'time':i*DAY,'open':100.,'high':101.,'low':99.,'close':100.,'volume':8.} for i in range(3)]})
  binding=ContextBinding(MarketContext('risk.daily','TEST:RISK','D','uploaded-file.1'),make_snapshot(higher,{'provider':'uploaded-file.1'}).sha256)
  for v in range(1,7):
   code=source(v,'entry')+f'plot({"request.security" if v>=5 else "security"}("TEST:RISK","D",close),title="Higher")\n'
   _,r=self.session.run(code,data=primary,primary_context=MarketContext('risk','TEST:RISK','180','uploaded-file.1'),context_sources=[(binding,MemorySource(higher))],broker_settings=EconomicsSettings.from_payload(SETTINGS))
   self.assertEqual(tuple(o.values for o in r.outputs[:3]),expected('entry',cycles=3)[0]);self.assertEqual(r.outputs[-1].values,tuple([100.]*24 if v<3 else [None]*7+[100.]*17))
 def test_preparation_freezes_risk_rules_even_without_script_parameters(self):
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   payload=request(manager);payload['input_ref']=manager.freeze_input({'source':source(),'source_name':'risk-no-input.pine'});payload['inputs']={}
   key=manager.submit(payload)['job_id'];environment.wait(manager,key);self.assertEqual(normalized(manager.result(key)['result']['simulation']['statistics']),expected('daily')[1])
 def test_companion_and_eighteen_examples_are_reproducible(self):
  from tools.risk_spec import prepare as release,RELEASE
  for n,b in release().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/n).read_bytes(),b)
  for n,b in prepare().items():self.assertEqual((ROOT/n).read_bytes(),b)
 def test_independent_corpus_exercises_every_risk_rule_in_every_version(self):
  from tools.conformance import run_cases
  from packages.contracts.risk import OPERATIONS
  r=run_cases(cases_path='conformance/risk/cases.json');self.assertEqual((r['executed_programs'],r['expected_rejections']),(36,60))
  for v in range(1,7):self.assertTrue(set(OPERATIONS)<=set(r['operations_by_version'][str(v)]))
 def test_observed_execution_rejects_risk_even_without_relative_exits(self):
  from packages.execution.realtime import ReplayEngine
  from packages.contracts.contexts import MarketContext
  for v in range(1,7):
   p=self.session.compile(source(v,body=''))
   with self.assertRaises(OpenPineError) as ctx:ReplayEngine(self.session,p,(MarketContext('risk','TEST:RISK','180','uploaded-file.1'),),'risk',object())
   self.assertEqual(ctx.exception.diagnostic.code,'PROTECTION_CONFIRMED_ONLY')
 def test_repinning_schema_does_not_authorize_foreign_rules_signatures_or_evidence(self):
  from tools.specification import encode,sha,load_snapshot,SpecError
  for changed in ('policy','apis','review','evidence'):
   with tempfile.TemporaryDirectory() as d:
    root=Path(d);shutil.copytree(ROOT/'spec',root/'spec');lock=read_json(root/'spec/lock.json');directory=root/lock['risk_release']['path'];value=read_json(directory/'risk.json')
    if changed=='policy':value['versions']['1']['policy']['liquidation']='future_high'
    elif changed=='apis':value['versions']['1']['apis'][0][1]='simulation.entry'
    elif changed=='review':value['review']['sources'][0]['facts']['effects']='unreviewed'
    else:value['versions']['1']['evidence']=value['versions']['6']['evidence']
    schema=read_json(root/'spec/schema/import.schema.json');schema['$defs']['risk_policy']['properties'].update(versions={'const':value['versions']},review={'const':value['review']})
    (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes());(directory/'risk.json').write_bytes(encode(value))
    manifest=read_json(directory/'manifest.json');manifest['files']['risk.json']=sha((directory/'risk.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest));lock['risk_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
    with self.assertRaises(SpecError):load_snapshot(root)

if __name__=='__main__':unittest.main()
