"""Disk history through worker resume, indexed pagination, backup and sealed tails."""
import copy,tempfile,unittest
from dataclasses import asdict
from pathlib import Path
from apps.openpine.bootstrap import local_manager
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.execution.session import Session
from packages.execution.driver import MemorySource
from packages.execution.environment import source_policy
from packages.marketdata.providers import make_snapshot
from packages.storage.trade_index import TradeIndex
from packages.storage.backup import backup,restore
from packages.storage.objects import ObjectStore
from packages.storage.results import ResultSink
from packages.contracts.wire import canonical,loads
from test_performance import source,market
from test_environment_pipeline import wait

def request(manager,v=6,code=None,data=None):
 return {'schema_version':'openpine.job-request.2','input_ref':manager.freeze_input({'source':code or source(v),'source_name':f'performance-v{v}.pine'}),
  'snapshot_id':manager.market.save(make_snapshot(data or market(),{'provider':'uploaded-file.1'})),'timeout_seconds':180}

class PerformancePipelineTests(unittest.TestCase):
 def test_eighteen_configured_workers_and_all_native_overrides_match_cycle_arithmetic(self):
  from tools.performance_fixtures import source as example,bars,SETTINGS,FAMILIES
  from tools.performance_oracle import expected
  from packages.marketdata import from_payload
  from packages.contracts.economics import EconomicsSettings
  session=Session(profile_id=HISTORICAL_PROFILE_ID,trade_history_factory=TradeIndex)
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   for v in range(1,7):
    for family in FAMILIES:
     code=example(v,family,inputs=True);data=from_payload(bars(family,3));p=session.compile(code)
     for units in (1,2):
      result=session.execute(p,data=data,inputs={'units':units},broker_settings=EconomicsSettings.from_payload(SETTINGS));values,stats=expected(family,units,3,v)
      self.assertEqual({o.title:list(o.values) for o in result.outputs},values,(v,family,units))
      actual=copy.deepcopy(result.simulation['statistics']);actual['performance']['archive'].pop('sha256');self.assertEqual(actual,stats)
     payload=request(manager,v,code,data);payload.update(schema_version='openpine.job-request.6',inputs={'units':1},broker_settings=SETTINGS)
     key=manager.submit(payload,pause_after=3)['job_id'];wait(manager,key,'paused');manager.resume(key);wait(manager,key)
     result=manager.result(key)['result'];values,stats=expected(family,1,3,v)
     self.assertEqual({o['title']:o['values'] for o in result['outputs']},values);self.assertEqual(manager.trades(key)['total'],stats['trades'])

 def test_six_workers_resume_index_and_paginated_history(self):
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   for v in range(1,7):
    code=source(v,extra='plot(strategy.closedtrades.profit(strategy.closedtrades-1),title="Last")\n' if v>=5 else '')
    key=manager.submit(request(manager,v,code),pause_after=3)['job_id'];wait(manager,key,'paused');manager.resume(key);wait(manager,key)
    result=manager.result(key)['result'];self.assertEqual(result['outputs'][0]['values'],[0.,0.,7.9,15.8,15.8,15.8,15.8,15.8]);self.assertEqual(manager.get(key)['resumed_from_bar'],3)
    first=manager.trades(key,0,1);second=manager.trades(key,1,1);self.assertEqual((first['total'],first['next'],second['next']),(2,1,2))
    for i,row in enumerate([first['trades'][0],second['trades'][0]]):self.assertEqual((row['closed_index'],row['net_profit'],row['commission'],row['entry_time'],row['exit_time']),(i,'7.9','2.1',60000,(i+2)*60000))
    self.assertEqual(manager.trades(key,2,100)['trades'],[])
    for start,limit in ((-1,1),(0,201),(True,1)):
     with self.assertRaises(OpenPineError):manager.trades(key,start,limit)
    chart=manager.chart(key,0,8);self.assertEqual(chart['schema_version'],'openpine.chart-window.10');marks=[e for e in chart['protection']['events'] if e['kind']=='mark'];self.assertEqual(marks[2]['value']['performance']['archive']['count'],1)
 def test_backup_rebuilds_history_before_later_read_and_close(self):
  code=source(extra='plot(strategy.closedtrades.profit(0),title="Oldest")\n')
  with tempfile.TemporaryDirectory() as d:
   root=Path(d)
   with local_manager(root/'live') as manager:
    key=manager.submit(request(manager,code=code),pause_after=3)['job_id'];wait(manager,key,'paused');backup(manager.db,manager.directory,root/'backup')
   restore(root/'backup',root/'restored')
   with local_manager(root/'restored') as manager:
    manager.resume(key);wait(manager,key);self.assertEqual(manager.trades(key)['total'],2);self.assertEqual(manager.result(key)['result']['outputs'][-1]['values'],[None,None,7.9,7.9,7.9,7.9,7.9,7.9])
 def test_sealed_parts_and_pending_csv_rebuild_one_history(self):
  session=Session(profile_id=HISTORICAL_PROFILE_ID);data=market();p=session.compile(source(extra='plot(strategy.closedtrades.profit(0),title="First")\n'));src=MemorySource(data);src.bind_identity()
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);objects=ObjectStore(root/'objects');sink=ResultSink(objects,root/'first');driver=session.stream_driver(p,src,sink);saved=[]
   try:
    self.assertTrue(driver.simulation.broker.performance.history.directory.is_relative_to(root/'first'))
    driver.run(pause_after=3,checkpoint=lambda x:saved.append(loads(canonical(x.checkpoint()))));sink.trade_sink.flush()
    output=sink.checkpoint(3);self.assertEqual(output['simulation']['schema_version'],'openpine.trade-prefix.2');self.assertEqual(output['simulation']['files'][0]['closed_rows'],1)
    second_sink=ResultSink(objects,root/'second');second=session.stream_driver(p,MemorySource(data),second_sink)
    try:
     second_sink.restore(output,3);second.restore(saved[0]);second.run();result=second_sink.finish(second.finish_stream(src.identity));self.assertEqual(result['simulation']['journal']['closed_records'],2);self.assertEqual(result['outputs'][-1]['last'],7.9)
    finally:second_sink.close()
   finally:sink.close()
 def test_repinning_runtime_hash_does_not_authorize_a_forged_closed_count(self):
  session=Session(profile_id=HISTORICAL_PROFILE_ID);data=market();p=session.compile(source());src=MemorySource(data)
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);objects=ObjectStore(root/'objects');sink=ResultSink(objects,root/'first');driver=session.stream_driver(p,src,sink);saved=[]
   try:
    driver.run(pause_after=4,checkpoint=lambda x:saved.append(loads(canonical(x.checkpoint()))));output=sink.checkpoint(4)
    bad=copy.deepcopy(saved[0]);bad['simulation']['broker']['performance']['archive']['count']=3
    other_sink=ResultSink(objects,root/'other');other=session.stream_driver(p,MemorySource(data),other_sink)
    try:
     other_sink.restore(output,4)
     with self.assertRaises(ValueError):other.restore(bad)
     self.assertEqual(other.simulation.broker.count,0)
    finally:other_sink.close()
    malformed=copy.deepcopy(output);malformed['simulation']['closed_records']=0;malformed['simulation']['pending']['closed_rows']=0
    meta_sink=ResultSink(objects,root/'metadata');meta=session.stream_driver(p,MemorySource(data),meta_sink)
    try:
     meta_sink.restore(malformed,4)
     with self.assertRaises(ValueError):meta.restore(saved[0])
     self.assertEqual(meta.simulation.broker.count,0)
    finally:meta_sink.close()
   finally:sink.close()
 def test_risk_forced_close_is_a_closed_trade_and_survives_resume(self):
  code=source(body='strategy.risk.max_drawdown(5,strategy.cash)\nif bar_index==0\n    strategy.entry("L",strategy.long)\n',extra='plot(strategy.closedtrades.profit(0),title="Stopped")\n')
  data=market((100,100,90,80,80,80,80,80))
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   key=manager.submit(request(manager,code=code,data=data),pause_after=3)['job_id'];wait(manager,key,'paused');manager.resume(key);wait(manager,key)
   page=manager.trades(key);self.assertEqual(page['total'],1);self.assertEqual(page['trades'][0]['net_profit'],'-23.8');self.assertTrue(manager.summary(key)['result']['simulation']['statistics']['risk']['blocked'])

if __name__=='__main__':unittest.main()
