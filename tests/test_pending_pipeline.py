"""Six language versions through compiled artifacts, workers and persistent entry recovery."""
import copy
from pathlib import Path
import tempfile
import unittest
from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.wire import canonical,loads
from packages.contracts.strategy_ir import broker_policy
from packages.execution.session import Session
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.marketdata import from_payload,data_identity
from packages.marketdata.providers import input_snapshot
from packages.storage.backup import backup,restore
from tools.specification import ROOT,read_json
from tools.pending_entry_fixtures import CYCLES,bars,source,prepare
from tools.pending_entry_oracle import expected
from tools.conformance import run_cases
import test_environment_pipeline as environment

def request(manager,version=6,family='stop-limit',part=7):
 root=ROOT/'examples/pending-entries'
 return {'schema_version':'openpine.job-request.6','input_ref':manager.freeze_input({'source':(root/f'{family}-v{version}.pine').read_text(),'source_name':f'{family}-v{version}.pine'}),
  'snapshot_id':manager.market.save(input_snapshot(read_json(root/f'{family}-bars.json'))),'inputs':{'part':part},'timeout_seconds':120}

class PendingPipelineTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

 def test_independent_corpus_covers_all_six_versions(self):
  report=run_cases(cases_path='conformance/pending-entries/cases.json')
  self.assertEqual((report['executed_programs'],report['expected_rejections']),(84,36))

 def test_every_pending_activation_fill_and_partial_boundary_roundtrips_all_versions(self):
  for v in range(1,7):
   for family in CYCLES:
    data=from_payload(bars(CYCLES[family]));text=(ROOT/f'conformance/pending-entries/v{v}-{family}-long.pine').read_text();program=self.session.compile(text)
    runtime=self.session.validate_program(program);baseline=self.session.execute(program,data=data)
    for cut in range(1,6):
     with self.subTest(v=v,family=family,cut=cut):
      def driver():return HistoricalDriver(runtime.prepare(program),MemorySource(data),simulation_policy=broker_policy(program))
      first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
      second=driver();second.restore(saved[0]);second.run();self.assertEqual(second.finish(data_identity(data)),baseline)

 def test_all_eighteen_workers_resume_pending_active_and_partly_closed_positions(self):
  with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
   for v in range(1,7):
    for family in CYCLES:
     with self.subTest(v=v,family=family):
      cut=2 if family=='stop-limit' else 3;key=manager.submit(request(manager,v,family),pause_after=cut)['job_id'];environment.wait(manager,key,'paused')
      manager.resume(key);state=environment.wait(manager,key);result=manager.result(key)['result']
      self.assertEqual(state['resumed_from_bar'],cut)
      for actual,reference in zip(result['outputs'],expected(family,7),strict=True):self.assertEqual(actual['values'],list(reference))
      self.assertEqual((result['simulation']['statistics']['trades'],result['simulation']['statistics']['closed_positions']),(160,80))
      chart=manager.chart(key,start=1,limit=4);self.assertEqual(chart['schema_version'],'openpine.chart-window.4')
      self.assertTrue(any(e['kind']=='entry_trigger' for e in chart['protection']['events']))

 def test_pending_and_stop_limit_activation_survive_backup_before_any_fill(self):
  for cut in (1,2):
   with tempfile.TemporaryDirectory() as directory:
    root=Path(directory)
    with local_manager(root/'live') as manager:
     key=manager.submit(request(manager),pause_after=cut)['job_id'];environment.wait(manager,key,'paused');backup(manager.db,manager.directory,root/'backup')
    restore(root/'backup',root/'restored')
    with local_manager(root/'restored') as manager:
     manager.resume(key);environment.wait(manager,key)
     self.assertEqual(manager.result(key)['result']['outputs'][1]['values'],expected('stop-limit',7)[1])

 def test_instrument_tick_is_bound_before_validating_nonzero_slippage(self):
  instrument={'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'ENTRY','type':'custom'}
  text='//@version=6\nstrategy("Tick first",default_qty_type=strategy.fixed,slippage=2)\nif bar_index==0\n    strategy.entry("L",strategy.long,stop=105*syminfo.mintick)\nplot(strategy.position_avg_price)'
  data=from_payload(bars(CYCLES['stop']))
  with tempfile.TemporaryDirectory() as directory,local_manager(directory) as manager:
   ref=manager.freeze_input({'source':text,'source_name':'tick.pine'})
   snapshot=manager.market.ingest({'context_id':data.context_id,'symbol':data.symbol,'timeframe':data.timeframe,'bar_schema':'openpine.historical-bars.2'},iter(data.bars),
    {'provider':'uploaded-file.1','environment':{'schema_version':'openpine.instrument-environment.1','instrument':instrument}})
   key=manager.submit({'schema_version':'openpine.job-request.6','input_ref':ref,'snapshot_id':snapshot,'inputs':{},'timeout_seconds':120},pause_after=2)['job_id']
   environment.wait(manager,key,'paused');manager.resume(key);environment.wait(manager,key)
   self.assertEqual(manager.plan(key).simulation_policy.settings.tick_size,'1')
   self.assertEqual(manager.result(key)['result']['outputs'][0]['values'],[None,107.,107.,107.,107.])

 def test_legacy_calls_retain_original_broker_and_na_levels_opt_in(self):
  for v in range(1,7):
   direction='true' if v==1 else 'strategy.long';text=source(v,f'if bar_index==0\n    strategy.entry("L",{direction})')
   self.assertEqual(broker_policy(self.session.compile(text)).profile_id,'openpine.market-broker.1')
   self.assertEqual(broker_policy(self.session.compile(text.replace(f'entry("L",{direction})',f'entry("L",{direction},limit=na)'))).profile_id,'openpine.pending-entry-broker.1')

 def test_when_versions_and_observed_execution_are_explicitly_bounded(self):
  from packages.execution.realtime import ReplayEngine
  from packages.contracts.contexts import MarketContext
  for v in range(1,7):
   direction='true' if v==1 else 'strategy.long';text=source(v,f'if bar_index==0\n    strategy.entry("L",{direction},limit=100'+(',when=false)' if v<6 else ')'))
   program=self.session.compile(text)
   if v<6:self.assertEqual(self.session.execute(program,data=from_payload(bars(CYCLES['limit']))).simulation['statistics']['orders'],0)
   else:
    with self.assertRaises(OpenPineError):self.session.compile(text.replace('limit=100','limit=100,when=true'))
   with self.assertRaises(OpenPineError) as caught:ReplayEngine(self.session,program,(MarketContext('entry','TEST:ENTRY','1','uploaded-file.1'),),'entry',object())
   self.assertEqual(caught.exception.diagnostic.code,'PROTECTION_CONFIRMED_ONLY')

 def test_release_and_all_examples_are_reproducible(self):
  from tools.pending_entry_spec import prepare as release,RELEASE
  for name,raw in release().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
  for name,raw in prepare().items():self.assertEqual((ROOT/name).read_bytes(),raw)

 def test_repinning_schema_cannot_authorize_foreign_evidence_or_changed_entry_abi(self):
  import shutil
  from tools.specification import encode,sha,load_snapshot,SpecError
  for change in ('policy','apis','evidence'):
   with tempfile.TemporaryDirectory() as directory:
    root=Path(directory);shutil.copytree(ROOT/'spec',root/'spec');lock=read_json(root/'spec/lock.json');target=root/lock['pending_entry_release']['path'];data=read_json(target/'pending-entries.json')
    if change=='policy':data['versions']['1']['policy']['max_pending_entries']=1000
    elif change=='apis':data['versions']['1']['apis'][0][1]='simulation.entry'
    else:data['versions']['1']['evidence']=data['versions']['6']['evidence']
    schema=read_json(root/'spec/schema/import.schema.json');schema['$defs']['pending_entry_policy']['properties']['versions']={'const':data['versions']}
    (root/'spec/schema/import.schema.json').write_bytes(encode(schema));lock['schema_sha256']=sha((root/'spec/schema/import.schema.json').read_bytes())
    (target/'pending-entries.json').write_bytes(encode(data));manifest=read_json(target/'manifest.json');manifest['files']['pending-entries.json']=sha((target/'pending-entries.json').read_bytes())
    (target/'manifest.json').write_bytes(encode(manifest));lock['pending_entry_release']['manifest_sha256']=sha((target/'manifest.json').read_bytes());(root/'spec/lock.json').write_bytes(encode(lock))
    with self.assertRaises(SpecError) as caught:load_snapshot(root)
    self.assertIn(caught.exception.code,('IMPLEMENTATION_MISSING','SPEC_PROVENANCE'))

if __name__=='__main__':unittest.main()
