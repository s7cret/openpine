"""Actual economic workers, exact fee accounting and joint durable recovery."""
import copy,shutil,tempfile,unittest
from pathlib import Path
from dataclasses import asdict
from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.economics import EconomicsSettings
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.contracts.strategy_ir import broker_policy
from packages.marketdata import from_payload,data_identity
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from tools.economics_fixtures import source,bars,SETTINGS,prepare,KINDS
from tools.economics_oracle import expected
from tools.specification import ROOT,read_json
import test_environment_pipeline as environment

def request(manager,v=6,family='cash',manual=0):
 root=ROOT/'examples/economics'
 return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':(root/f'{family}-v{v}.pine').read_text(),'source_name':f'{family}-v{v}.pine'}),
 'snapshot_id':manager.market.save(input_snapshot(read_json(root/f'{family}-input.json'))),'inputs':{'manual':float(manual)},'timeout_seconds':180}

class EconomicsPipelineTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
 def test_all_versions_modes_directions_and_override_match_independent_cycle_accounting(self):
  for v in range(1,7):
   for f in KINDS:
    for manual in (0,2):
     for short in (False,True):
      with self.subTest(v=v,f=f,manual=manual,short=short):
       p,r=self.session.run(source(v,f,manual=str(manual) if manual else 'na',short=short),data=from_payload(bars(f,short=short)),broker_settings=EconomicsSettings.from_payload(SETTINGS))
       values,stats=expected(f,manual,1,short)
       self.assertEqual(tuple(o.values for o in r.outputs),values)
       for name in ('cash_exact','equity_exact','gross_realized_exact','commission','entry_commission','exit_commission','orders','fills','trades','closed_lots','closed_positions','economics'):self.assertEqual(r.simulation['statistics'][name],stats[name],name)
 def test_all_activation_partial_fill_and_sizing_boundaries_restore_every_version(self):
  for v in range(1,7):
   for f in KINDS:
    p=self.session.compile(source(v,f));runtime=self.session.validate_program(p);policy=broker_policy(p,EconomicsSettings.from_payload(SETTINGS));market=from_payload(bars(f));baseline=self.session.execute(p,data=market,broker_settings=policy.settings)
    for cut in range(1,9):
     def driver():return HistoricalDriver(runtime.prepare(p),MemorySource(market),simulation_policy=policy)
     first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
     resumed=driver();resumed.restore(saved[0]);resumed.run();self.assertEqual(resumed.finish(data_identity(market)),baseline,(v,f,cut))
 def test_eighteen_real_workers_pin_rules_resume_and_expose_quantity_and_fee_events(self):
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   for v in range(1,7):
    for f in KINDS:
     with self.subTest(v=v,f=f):
      cut={'cash':2,'equity':4,'flat':3}[f];key=manager.submit(request(manager,v,f),pause_after=cut)['job_id'];environment.wait(manager,key,'paused');manager.resume(key);state=environment.wait(manager,key)
      self.assertEqual(state['resumed_from_bar'],cut);result=manager.result(key)['result'];values,stats=expected(f)
      self.assertEqual(tuple(tuple(o['values']) for o in result['outputs']),values)
      self.assertEqual(result['simulation']['statistics']['equity_exact'],stats['equity_exact'])
      self.assertEqual(asdict(manager.plan(key).simulation_policy.settings),SETTINGS)
      chart=manager.chart(key,start=0,limit=8);self.assertEqual(chart['schema_version'],'openpine.chart-window.8')
      events=chart['protection']['events'];self.assertTrue(any(e['kind']=='quantity_resolved' for e in events));self.assertTrue(any(e['kind']=='commission_calculated' for e in events))
      self.assertLessEqual(chart['protection']['event_bytes'],chart['protection']['event_byte_budget'])
 def test_backup_preserves_flat_fee_count_partial_lot_and_later_trailing_exit(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   with local_manager(root/'live') as manager:
    key=manager.submit(request(manager,family='flat'),pause_after=3)['job_id'];environment.wait(manager,key,'paused');backup(manager.db,manager.directory,root/'backup')
   restore(root/'backup',root/'restored')
   with local_manager(root/'restored') as manager:
    manager.resume(key);state=environment.wait(manager,key);self.assertEqual(state['resumed_from_bar'],3)
    result=manager.result(key)['result'];values,stats=expected('flat');self.assertEqual(tuple(tuple(o['values']) for o in result['outputs']),values)
    self.assertEqual(result['simulation']['statistics']['economics'],stats['economics'])
 def test_rules_and_program_policy_cannot_be_changed_after_preparation(self):
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   payload=request(manager);payload['broker_settings']={**SETTINGS,'minimum_notional':'100'}
   with self.assertRaises(OpenPineError):manager.submit(payload)
 def test_new_companion_and_all_examples_are_reproducible(self):
  from tools.economics_spec import prepare as release,RELEASE
  for name,raw in release().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
  for name,raw in prepare().items():self.assertEqual((ROOT/name).read_bytes(),raw)
 def test_economic_declaration_becomes_private_indicator_in_every_requested_context(self):
  from packages.contracts.contexts import MarketContext,ContextBinding
  from packages.marketdata.providers import make_snapshot
  primary=from_payload(bars('cash'));higher_data={**bars('cash'),'context_id':'economic.higher','timeframe':'4','bars':[{'time':i*240000,'open':100.,'high':101.,'low':99.,'close':100.,'volume':4.} for i in range(2)]};higher=from_payload(higher_data)
  context=MarketContext('economic.higher','TEST:ECON','4','uploaded-file.1');binding=ContextBinding(context,make_snapshot(higher,{'provider':'uploaded-file.1'}).sha256)
  for v in range(1,7):
   code=source(v,'cash');call='request.security' if v>=5 else 'security';code=code.replace('if bar_index==0',f'higher={call}("TEST:ECON","4",close)\nif bar_index==0')+'plot(higher)\n'
   _,r=self.session.run(code,data=primary,primary_context=MarketContext('economics','TEST:ECON','1','uploaded-file.1'),context_sources=[(binding,MemorySource(higher))],broker_settings=EconomicsSettings.from_payload(SETTINGS))
   self.assertEqual(tuple(o.values for o in r.outputs[:3]),expected('cash',cycles=1)[0]);self.assertEqual(r.outputs[-1].values,tuple([100.]*8 if v<3 else [None]*3+[100.]*5))
 def test_preparation_freezes_economic_program_even_without_input_declarations(self):
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   payload=request(manager);payload['input_ref']=manager.freeze_input({'source':source(6,'cash'),'source_name':'cash-no-input.pine'});payload['inputs']={}
   self.assertEqual(set(manager.objects.get(payload['input_ref'])),{'artifact_ref','source_ref'})
   key=manager.submit(payload)['job_id'];environment.wait(manager,key);stats=manager.result(key)['result']['simulation']['statistics']
   self.assertEqual((stats['orders'],stats['equity_exact']),(2,'1007.9'))
 def test_independent_corpus_has_explicit_all_version_execution_and_rejections(self):
  from tools.conformance import run_cases
  report=run_cases(cases_path='conformance/economics/cases.json');self.assertEqual((report['executed_programs'],report['expected_rejections']),(78,43))
  for v in range(1,7):self.assertIn('simulation.strategy_economics',report['operations_by_version'][str(v)])
 def test_observed_execution_remains_explicitly_outside_confirmed_profile(self):
  from packages.execution.realtime import ReplayEngine
  from packages.contracts.contexts import MarketContext
  for v in range(1,7):
   p=self.session.compile(source(v,'cash',body=''))
   with self.assertRaises(OpenPineError) as caught:ReplayEngine(self.session,p,(MarketContext('economics','TEST:ECON','1','uploaded-file.1'),),'economics',object())
   self.assertEqual(caught.exception.diagnostic.code,'PROTECTION_CONFIRMED_ONLY')
 def test_schema_repin_cannot_authorize_foreign_economics_or_evidence(self):
  from tools.specification import encode,sha,load_snapshot,SpecError
  for changed in ('policy','apis','review','evidence'):
   with tempfile.TemporaryDirectory() as d:
    root=Path(d);shutil.copytree(ROOT/'spec',root/'spec');lock=read_json(root/'spec/lock.json');directory=root/lock['economics_release']['path'];value=read_json(directory/'economics.json')
    if changed=='policy':value['versions']['1']['policy']['quantity_basis']='future_close'
    elif changed=='apis':value['versions']['1']['apis'][0][1]='simulation.strategy'
    elif changed=='review':value['review']['sources'][0]['facts']['modes']='unreviewed'
    else:value['versions']['1']['evidence']=value['versions']['6']['evidence']
    schema=read_json(root/'spec/schema/import.schema.json');schema['$defs']['economics_policy']['properties'].update(versions={'const':value['versions']},review={'const':value['review']})
    (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
    (directory/'economics.json').write_bytes(encode(value));manifest=read_json(directory/'manifest.json');manifest['files']['economics.json']=sha((directory/'economics.json').read_bytes());(directory/'manifest.json').write_bytes(encode(manifest));lock['economics_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
    with self.assertRaises(SpecError):load_snapshot(root)

if __name__=='__main__':unittest.main()
