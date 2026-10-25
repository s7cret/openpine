"""Independent accounting, visibility, random access and portable history boundaries."""
import copy,tempfile,unittest
from dataclasses import asdict
from pathlib import Path
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.performance import PerformancePolicy
from packages.contracts.trading import BrokerPolicy
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.environment import source_policy
from packages.marketdata import from_payload,data_identity
from packages.storage.trade_index import TradeIndex

def market(prices=(100,100,110,110,105,100,100,100)):
 return from_payload({'schema_version':'openpine.historical-bars.2','context_id':'performance','symbol':'TEST:PERFORMANCE','timeframe':'1',
  'bars':[{'time':i*60000,'open':float(p),'close':float(p),'high':float(p+1),'low':float(p-1),'volume':1.} for i,p in enumerate(prices)]})
def source(v=6,body=None,extra='',fees='1'):
 body=body if body is not None else f'if bar_index==0\n    strategy.entry("L",{"true" if v<5 else "strategy.long"})\nif bar_index==1\n    strategy.close("L",qty=1)\nif bar_index==2\n    strategy.close("L")\n'
 return f'//@version={v}\nstrategy("Trade observations",default_qty_type=strategy.fixed,default_qty_value=2,initial_capital=1000,commission_value={fees})\n'+body+'''plot(strategy.netprofit,title="Net")
plot(strategy.openprofit,title="Open")
plot(strategy.closedtrades,title="Closed")
plot(strategy.opentrades,title="Active")
plot(strategy.grossprofit,title="Wins")
plot(strategy.grossloss,title="Losses")
plot(strategy.wintrades,title="Win count")
plot(strategy.losstrades,title="Loss count")
'''+extra

class PerformanceTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID,trade_history_factory=TradeIndex)
 def run_code(self,code,data=None):return self.session.run(code,data=data or market())[1]
 def driver(self,code,data=None):
  data=data or market();p=self.session.compile(code);runtime=self.session.validate_program(p);src=MemorySource(data)
  return HistoricalDriver(runtime.prepare(p),src,simulation_policy=source_policy(p,src),trade_history_factory=TradeIndex)
 def test_six_versions_partial_slices_and_open_fee_identity(self):
  for v in range(1,7):
   result=self.run_code(source(v));values={o.title:o.values for o in result.outputs}
   self.assertEqual(values['Net'],(0.,0.,7.9,15.8,15.8,15.8,15.8,15.8))
   self.assertEqual(values['Open'],(0.,-2.,9.,0.,0.,0.,0.,0.))
   self.assertEqual(values['Closed'],(0,0,1,2,2,2,2,2));self.assertEqual(values['Active'],(0,1,1,0,0,0,0,0))
   self.assertEqual(values['Win count'],values['Closed']);self.assertEqual(values['Loss count'],(0,)*8)
   marks=[e['value'] for e in result.simulation['events'] if e['kind']=='mark']
   for i,m in enumerate(marks):self.assertAlmostEqual(float(m['equity_exact']),1000+values['Net'][i]+values['Open'][i])
   st=result.simulation['statistics'];self.assertEqual(st['closed_lots'],1);self.assertEqual(st['closed_positions'],1);self.assertEqual(st['performance']['archive']['count'],2)
 def test_fee_only_losses_and_zero_fee_breakeven_are_distinct(self):
  for fee,loss,evens in (('1',4.,0),('0',0.,2)):
   result=self.run_code(source(fees=fee),market((100,)*8));st=result.simulation['statistics']['performance']
   self.assertEqual((st['wins'],st['losses'],st['evens']),(0,2 if loss else 0,evens));self.assertEqual(float(st['netprofit']),-loss);self.assertEqual(float(st['grossloss']),loss)
 def test_pyramiding_partial_then_global_close_has_three_allocated_trades(self):
  code='''//@version=6
strategy("Multi",pyramiding=2,default_qty_type=strategy.fixed,commission_type=strategy.commission.cash_per_order,commission_value=1,initial_capital=1000)
if bar_index==0
    strategy.entry("A",strategy.long,qty=2)
if bar_index==1
    strategy.entry("B",strategy.long,qty=1)
    strategy.close("A",qty=1)
if bar_index==2
    strategy.close_all()
plot(strategy.closedtrades,title="Closed")
plot(strategy.opentrades.entry_price(0),title="First entry")
plot(strategy.opentrades.entry_price(1),title="Second entry")
plot(strategy.netprofit,title="Net")
'''
  result=self.run_code(code);values={o.title:o.values for o in result.outputs};st=result.simulation['statistics']
  self.assertEqual(values['Closed'][:4],(0,0,1,3));self.assertEqual(values['First entry'][2],100.);self.assertEqual(values['Second entry'][2],110.)
  self.assertEqual((st['closed_lots'],st['closed_positions'],st['performance']['wins'],st['performance']['losses']),(2,1,2,1));self.assertEqual(st['performance']['netprofit'],'16')
 def test_short_closed_size_is_signed_and_profit_uses_short_price_direction(self):
  code=source(body='if bar_index==0\n    strategy.entry("S",strategy.short)\nif bar_index==1\n    strategy.close("S",qty=1)\nif bar_index==2\n    strategy.close("S")\n',fees='0',extra='plot(strategy.closedtrades.size(0),title="Size")\nplot(strategy.closedtrades.profit(1),title="Second loss")\n')
  values={o.title:o.values for o in self.run_code(code).outputs};self.assertEqual(values['Size'][-1],-1.);self.assertEqual(values['Second loss'][-1],-10.);self.assertEqual(values['Net'][-1],-20.)
 def test_reports_distinguish_closed_profit_from_remaining_open_exposure(self):
  from packages.optimizer.reporting import strategy_metrics
  result=self.run_code(source(body='if bar_index==0\n    strategy.entry("L",strategy.long)\n'),market((100,100,110,110)))
  metrics=strategy_metrics({'simulation':result.simulation},result.simulation['events'])['values']
  self.assertEqual((metrics['net_profit'],metrics['closed_net_profit'],metrics['open_net_profit']),('18','0','18'))
 def test_numeric_readers_preserve_original_entry_and_allocated_exit(self):
  for v in (5,6):
   fields={'size':(1.,1.),'entry_price':(100.,100.),'exit_price':(110.,110.),'profit':(7.9,7.9),'commission':(2.1,2.1),'entry_bar_index':(1,1),'exit_bar_index':(2,3),'entry_time':(60000,60000),'exit_time':(120000,180000)}
   extra=''.join(f'plot(strategy.closedtrades.{name}({i}),title="{name}{i}")\n' for name in fields for i in (0,1))
   result=self.run_code(source(v,extra=extra));values={o.title:o.values for o in result.outputs}
   for name,wants in fields.items():
    for i,want in enumerate(wants):self.assertEqual(values[name+str(i)][-1],want);self.assertIsNone(values[name+str(i)][1])
 def test_open_readers_follow_remaining_quantity_and_remaining_commission(self):
  result=self.run_code(source(extra=''.join(f'plot(strategy.opentrades.{name}(0),title="{name}")\n' for name in ('size','profit','commission','entry_price','entry_time'))))
  values={o.title:o.values for o in result.outputs}
  self.assertEqual(values['size'],(None,2.,1.,None,None,None,None,None));self.assertEqual(values['commission'][1:3],(2.,1.));self.assertEqual(values['profit'][1:3],(-2.,9.))
  self.assertEqual(values['entry_time'][1:3],(60000,60000))
 def test_index_na_negative_future_and_dynamic_index(self):
  extra='plot(strategy.closedtrades.profit(-1),title="Negative")\nplot(strategy.closedtrades.profit(100),title="Future")\nplot(strategy.closedtrades.profit(int(na)),title="Missing")\nplot(strategy.closedtrades.profit(strategy.closedtrades-1),title="Last")\n'
  values={o.title:o.values for o in self.run_code(source(extra=extra)).outputs}
  for name in ('Negative','Future','Missing'):self.assertEqual(values[name],(None,)*8)
  self.assertEqual(values['Last'],(None,None,7.9,7.9,7.9,7.9,7.9,7.9))
 def test_early_readers_and_float_indices_are_rejected(self):
  for v in range(1,5):
   with self.assertRaises(OpenPineError):self.session.compile(source(v,extra='plot(strategy.closedtrades.profit(0))\n'))
  with self.assertRaises(OpenPineError):self.session.compile(source(extra='plot(strategy.closedtrades.profit(0.5))\n'))
 def test_readers_require_strategy_and_primary_history(self):
  with self.assertRaises(OpenPineError):self.session.compile('//@version=6\nindicator("x")\nplot(strategy.closedtrades.profit(0))\n')
  with self.assertRaises(OpenPineError):self.session.compile(source(extra='plot(request.security("TEST:PERFORMANCE","D",strategy.closedtrades.profit(0)))\n'))
 def test_history_and_udf_observations_are_stable(self):
  result=self.run_code(source(extra='last() => strategy.closedtrades.profit(strategy.closedtrades-1)\nplot(last(),title="Last")\nplot(strategy.netprofit[1],title="Prior")\n'))
  values={o.title:o.values for o in result.outputs};self.assertEqual(values['Prior'],(None,0.,0.,7.9,15.8,15.8,15.8,15.8));self.assertEqual(values['Last'][2],7.9)
 def test_closed_policy_payload_rejects_unknown_and_incomplete_fields(self):
  p=self.driver(source())
  try:
   policy=p.simulation.broker.policy;self.assertIs(type(policy),PerformancePolicy);self.assertEqual(BrokerPolicy.from_payload(asdict(policy)),policy)
   for payload in ({**asdict(policy),'extra':1},{k:v for k,v in asdict(policy).items() if k!='timeframe'}):
    with self.assertRaises(ValueError):BrokerPolicy.from_payload(payload)
  finally:p.simulation.close()
 def test_every_memory_boundary_rebuilds_identical_history_and_result(self):
  for v in range(1,7):
   code=source(v,extra='plot(strategy.closedtrades.profit(strategy.closedtrades-1),title="Last")\n' if v>=5 else '');data=market();baseline=self.run_code(code,data)
   for cut in range(1,9):
    first=self.driver(code,data);second=self.driver(code,data);saved=[]
    try:
     first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))));second.restore(saved[0]);second.run()
     self.assertEqual(second.finish(data_identity(data)),baseline,(v,cut))
    finally:first.simulation.close();second.simulation.close()
 def test_consistently_tampered_counters_or_hash_fail_before_broker_assignment(self):
  first=self.driver(source());saved=[]
  try:first.run(pause_after=4,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
  finally:first.simulation.close()
  for kind in ('wins','archive','netprofit'):
   bad=copy.deepcopy(saved[0]);st=bad['simulation']['broker']['performance']
   if kind=='archive':st[kind]['sha256']='0'*64
   elif kind=='wins':st[kind]+=1
   else:st[kind]='16.8'
   destination=self.driver(source())
   try:
    with self.assertRaises(ValueError):destination.restore(bad)
    self.assertEqual(destination.simulation.broker.count,0);self.assertIsNone(destination.simulation.broker.position)
   finally:destination.simulation.close()
 def test_disk_lookup_keeps_bounded_cache_and_can_read_oldest_row(self):
  index=TradeIndex();directory=index.directory
  try:
   for i in range(20000):index.append({'closed_index':i,'profit':str(i)})
   self.assertLessEqual(len(index.cache),128);self.assertEqual(index.read(0)['profit'],'0');self.assertEqual(index.read(19999)['profit'],'19999');self.assertIsNone(index.read(20000))
  finally:index.close()
  self.assertFalse(directory.exists())
 def test_many_reads_have_a_per_bar_budget_even_for_missing_indices(self):
  code=source(extra='for i=0 to 4096\n    x=strategy.closedtrades.profit(i)\n')
  with self.assertRaises(OpenPineError) as raised:self.run_code(code)
  self.assertEqual(raised.exception.diagnostic.code,'TRADE_READ_BUDGET')

if __name__=='__main__':unittest.main()
