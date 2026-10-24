"""Lot declarations through all versioned compilers, workers and checkpoint cuts."""
import copy,tempfile,unittest
from pathlib import Path
from dataclasses import replace
from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalData,HistoricalBar,OpenPineError
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.marketdata import data_identity,from_payload
from packages.marketdata.providers import make_snapshot,input_snapshot
from packages.storage.backup import backup,restore
from tools.specification import ROOT,read_json
from tools.lot_fixtures import CYCLES,bars,source as example_source,example_body,prepare
from tools.lot_oracle import expected as example_expected,cycle_totals
import test_environment_pipeline as environment

def data():
 return HistoricalData('lots','TEST:LOTS','1',tuple(HistoricalBar(i*60000,p,p,p,p,1.) for i,p in enumerate((100.,100.,110.,120.,90.))),schema_version='openpine.historical-bars.2')

def source(v=6):
 direction='true' if v==1 else 'strategy.long'
 return f'//@version={v}\nstrategy("Lots",pyramiding=2,default_qty_type=strategy.fixed,initial_capital=1000,commission_value=1)\nif bar_index==0\n    strategy.entry("L",{direction},qty=2)\nif bar_index==1\n    strategy.entry("L",{direction},qty=3)\nif bar_index==2\n    strategy.close("L",qty=4)\nif bar_index==3\n    strategy.close("L")\nplot(strategy.position_size,title="Position")\nplot(strategy.equity,title="Equity")\nplot(strategy.position_avg_price,title="Entry price")'

EXPECTED=((0.,2.,5.,1.,0.),(1000.,998.,1014.7,1059.9,1029.),(None,100.,106.,110.,None))

def request(manager,v=6):
 return {'schema_version':'openpine.job-request.2','input_ref':manager.freeze_input({'source':source(v),'source_name':f'lots-v{v}.pine'}),
  'snapshot_id':manager.market.save(make_snapshot(data(),{'provider':'uploaded-file.1'})),'timeout_seconds':120}

def example_request(manager,v=6,family='conditional',part=1):
 root=ROOT/'examples/lots'
 return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':(root/f'{family}-v{v}.pine').read_text(),'source_name':f'{family}-v{v}.pine'}),
  'snapshot_id':manager.market.save(input_snapshot(read_json(root/f'{family}-bars.json'))),'inputs':{'part':part},'timeout_seconds':120}

class LotPipelineTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

 def test_all_versions_independent_quantities_costs_and_fee_oracle(self):
  for v in range(1,7):
   with self.subTest(version=v):
    p,r=self.session.run(source(v),data=data());self.assertEqual(tuple(o.values for o in r.outputs),EXPECTED)
    self.assertEqual(p.statements[0].operation,'simulation.strategy_lots');self.assertIn(f'openpine.lots.v{v}.1',p.identity.semantic_profile_id.split('+'))
    self.assertEqual(r.simulation['schema_version'],'openpine.simulation.5');self.assertEqual(r.simulation['statistics']['cash_exact'],'1029')

 def test_all_versions_every_multi_lot_partial_boundary_roundtrips(self):
  for v in range(1,7):
   p=self.session.compile(source(v));runtime=self.session.validate_program(p);expected=self.session.execute(p,data=data())
   for cut in range(1,6):
    with self.subTest(version=v,cut=cut):
     def driver():return HistoricalDriver(runtime.prepare(p),MemorySource(data()),simulation_policy=broker_policy(p))
     a=driver();saved=[];a.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
     b=driver();b.restore(saved[0]);b.run();self.assertEqual(b.finish(data_identity(data())),expected)

 def test_all_eighteen_native_workers_restore_lots_reservations_and_range_chart(self):
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   for v in range(1,7):
    for family in CYCLES:
     with self.subTest(version=v,family=family):
      cut=5 if family=='conditional' else 3;key=manager.submit(example_request(manager,v,family),pause_after=cut)['job_id'];environment.wait(manager,key,'paused');manager.resume(key);state=environment.wait(manager,key)
      self.assertEqual(state['resumed_from_bar'],cut);r=manager.result(key)['result'];self.assertEqual(tuple(tuple(o['values']) for o in r['outputs']),example_expected(family,1))
      chart=manager.chart(key,start=1,limit=6);self.assertEqual(chart['schema_version'],'openpine.chart-window.5')
      marks=[e for e in chart['protection']['events'] if e['kind']=='mark'];self.assertTrue(any(len(e['value']['lots'])==2 for e in marks))
      self.assertEqual(manager.plan(key).simulation_policy.pyramiding,3)

 def test_backup_before_partial_exit_preserves_multiple_lots_and_frozen_targets(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   with local_manager(root/'live') as manager:
    key=manager.submit(request(manager),pause_after=3)['job_id'];environment.wait(manager,key,'paused');backup(manager.db,manager.directory,root/'backup')
   restore(root/'backup',root/'restored')
   with local_manager(root/'restored') as manager:
    manager.resume(key);environment.wait(manager,key);self.assertEqual(manager.result(key)['result']['outputs'][1]['values'],list(EXPECTED[1]))

 def test_legacy_declarations_keep_prior_broker_and_invalid_caps_reject(self):
  for v in range(1,7):
   legacy=source(v).replace('pyramiding=2,','').replace(',qty=4','')
   self.assertEqual(broker_policy(self.session.compile(legacy)).profile_id,'openpine.market-broker.1')
   explicit=legacy.replace('default_qty_type=', 'pyramiding=0,default_qty_type=');self.assertEqual(broker_policy(self.session.compile(explicit)).profile_id,'openpine.market-broker.1')
   for bad in (source(v).replace('pyramiding=2','pyramiding=17'),source(v).replace('pyramiding=2','pyramiding=2,close_entries_rule="LIFO"')):
    with self.assertRaises(OpenPineError):self.session.compile(bad)

 def test_independent_corpus_covers_four_order_kinds_and_selection_all_versions(self):
  from tools.conformance import run_cases
  report=run_cases(cases_path='conformance/lots/cases.json');self.assertEqual((report['executed_programs'],report['expected_rejections']),(114,36))

 def test_all_families_activation_partial_and_cleanup_cuts_match_closed_form(self):
  for v in range(1,7):
   for family,rows in CYCLES.items():
    market=from_payload(bars(rows));text=example_source(v,example_body(v,family,repeat=False,part='1'),rule='FIFO' if family=='fifo' else 'ANY')
    p=self.session.compile(text);runtime=self.session.validate_program(p);baseline=self.session.execute(p,data=market)
    self.assertEqual(tuple(o.values for o in baseline.outputs),example_expected(family,1,1))
    for cut in range(1,len(rows)+1):
     with self.subTest(version=v,family=family,cut=cut):
      def driver():return HistoricalDriver(runtime.prepare(p),MemorySource(market),simulation_policy=broker_policy(p))
      first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
      second=driver();second.restore(saved[0]);second.run();self.assertEqual(second.finish(data_identity(market)),baseline)

 def test_pinned_release_and_examples_are_reproducible(self):
  from tools.lot_spec import prepare as release,RELEASE
  for name,raw in release().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
  for name,raw in prepare().items():self.assertEqual((ROOT/name).read_bytes(),raw)

 def test_repinning_schema_does_not_authorize_changed_lot_abi_or_evidence(self):
  import shutil
  from tools.specification import encode,sha,load_snapshot,SpecError
  for change in ('policy','apis','evidence'):
   with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);shutil.copytree(ROOT/'spec',root/'spec');lock=read_json(root/'spec/lock.json');target=root/lock['lot_release']['path'];payload=read_json(target/'lots.json')
    if change=='policy':payload['versions']['1']['policy']['max_open_lots']=1000
    elif change=='apis':payload['versions']['1']['apis'][0][1]='simulation.strategy'
    else:payload['versions']['1']['evidence']=payload['versions']['6']['evidence']
    schema=read_json(root/'spec/schema/import.schema.json');schema['$defs']['lot_policy']['properties']['versions']={'const':payload['versions']}
    (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
    (target/'lots.json').write_bytes(encode(payload));manifest=read_json(target/'manifest.json');manifest['files']['lots.json']=sha((target/'lots.json').read_bytes())
    (target/'manifest.json').write_bytes(encode(manifest));lock['lot_release']['manifest_sha256']=sha((target/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
    with self.assertRaises(SpecError) as caught:load_snapshot(root)
    self.assertIn(caught.exception.code,('IMPLEMENTATION_MISSING','SPEC_PROVENANCE'))

 def test_observed_execution_stays_explicitly_outside_lot_profile(self):
  from packages.execution.realtime import ReplayEngine
  from packages.contracts.contexts import MarketContext
  for v in range(1,7):
   p=self.session.compile(source(v))
   with self.assertRaises(OpenPineError) as caught:ReplayEngine(self.session,p,(MarketContext('lots','TEST:LOTS','1','uploaded-file.1'),),'lots',object())
   self.assertEqual(caught.exception.diagnostic.code,'PROTECTION_CONFIRMED_ONLY')

if __name__=='__main__':unittest.main()
