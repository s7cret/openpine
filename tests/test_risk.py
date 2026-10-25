"""Risk chronology, exact costs, entry scope and atomic durable state."""
import copy,unittest
from dataclasses import asdict,replace
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.risk import RiskPolicy,RiskRule,RiskLimits
from packages.contracts.economics import EconomicsSettings
from packages.contracts.trading import BrokerPolicy
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.environment import source_policy
from packages.marketdata import from_payload,data_identity
from tools.risk_fixtures import source,bars,SETTINGS
from tools.risk_oracle import expected,DAY

def data(rows,times=None,timeframe='1'):
 return from_payload({'schema_version':'openpine.historical-bars.2','context_id':'risk','symbol':'TEST:RISK','timeframe':timeframe,'bars':[
  {'time':times[i] if times else i*60000,'open':o,'high':h,'low':l,'close':c,'volume':1.} for i,(o,h,l,c) in enumerate(rows)]})
FLAT=(100,101,99,100)

class RiskTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
 def run_code(self,code,market,settings=None):return self.session.run(code,data=market,broker_settings=settings or EconomicsSettings('1','1'))[1]
 def driver(self,code,market,window=None):
  p=self.session.compile(code);runtime=self.session.validate_program(p);src=MemorySource(market)
  if window is not None:src.bind_identity()
  return HistoricalDriver(runtime.prepare(p),src,simulation_policy=source_policy(p,src,EconomicsSettings('1','1')),evaluation_window=window)
 def test_policy_is_closed_and_economic_wire_abi_is_preserved(self):
  rule=RiskRule('max_drawdown','call:r','5','cash');p=RiskPolicy('1000','1','0',0,EconomicsSettings('1','1'),limits=RiskLimits(max_drawdown=rule))
  self.assertEqual(BrokerPolicy.from_payload(asdict(p)),p)
  for value in ({**asdict(p),'unexpected':1},{**asdict(p),'limits':{}},{**asdict(p),'timeframe':'4H'}):
   with self.assertRaises(ValueError):BrokerPolicy.from_payload(value)
  for args in [('max_position_size','call:r','0'),('max_drawdown','call:r','101','percent_of_equity'),('max_intraday_filled_orders','call:r',True)]:
   with self.assertRaises(ValueError):RiskRule(*args)
 def test_all_six_rules_versions_and_overrides_match_independent_arithmetic(self):
  for v in range(1,7):
   for f in ('entry','daily','lifetime'):
    for manual in (0,1):
     with self.subTest(v=v,f=f,manual=manual):
      r=self.run_code(source(v,f,manual='1' if manual else 'na'),from_payload(bars(f,3)),EconomicsSettings.from_payload(SETTINGS));values,stats=expected(f,manual,3)
      self.assertEqual(tuple(o.values for o in r.outputs),values)
      got=copy.deepcopy(r.simulation['statistics'])
      if got['risk']['halt']:got['risk']['halt'].pop('call_site_id')
      self.assertEqual(got,stats)
 def test_close_observation_defers_to_next_open_and_keeps_last_bar_exposure(self):
  code=source(rules='strategy.risk.max_drawdown(5,strategy.cash)\n',body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=1)\n')
  market=data((FLAT,(100,101,89,90),(80,200,79,200)))
  before=self.run_code(code,replace(market,bars=market.bars[:2]));self.assertTrue(before.simulation['statistics']['risk']['needs_close']);self.assertEqual(before.simulation['statistics']['fills'],1)
  r=self.run_code(code,market);st=r.simulation['statistics'];self.assertEqual((st['cash_exact'],st['fills'],st['risk']['halt']['basis']),('978',2,'close'))
  self.assertEqual([e['bar_index'] for e in r.simulation['events'] if e['kind']=='risk_liquidation'],[2])
 def test_open_gap_stops_before_pending_candidate_and_does_not_use_future_close(self):
  code=source(rules='strategy.risk.max_drawdown(5,strategy.cash)\n',body='if bar_index==0\n    strategy.entry("A",strategy.long,qty=2)\nif bar_index==1\n    strategy.entry("B",strategy.long,qty=2)\n')
  r=self.run_code(code,data((FLAT,FLAT,(80,200,79,200))));st=r.simulation['statistics']
  self.assertEqual((st['cash_exact'],st['fills'],st['risk']['halt']['basis']),('958',2,'open'));self.assertEqual(st['entries'],[])
  self.assertFalse(any(e['kind']=='fill' and e['value']['entry_id']=='B' for e in r.simulation['events']))
 def test_after_fill_risk_marks_the_known_reference_with_normal_slippage(self):
  code=source(rules='strategy.risk.max_drawdown(1,strategy.cash)\n',body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=2)\n',header=',slippage=2')
  r=self.run_code(code,data((FLAT,FLAT)));self.assertEqual(r.simulation['statistics']['cash_exact'],'990')
  self.assertEqual([e['value']['price'] for e in r.simulation['events'] if e['kind']=='fill'],['102','98'])
 def test_direction_close_only_and_cap_do_not_restrict_net_orders(self):
  r=self.run_code(source(family='entry'),from_payload(bars('entry',1)));fills=[e['value'] for e in r.simulation['events'] if e['kind']=='fill']
  self.assertTrue(any(f['entry_id']=='N' and f['direction']==-1 and f['quantity']=='4' for f in fills))
  self.assertFalse(any(e['value']['entry_id']=='S' for e in r.simulation['events'] if e['kind']=='trade'));self.assertEqual(r.simulation['statistics']['equity_exact'],'1080')
 def test_cap_shrinks_pending_protection_and_below_step_costs_nothing(self):
  code=source(rules='strategy.risk.max_position_size(3)\n',body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=5,limit=100)\n    strategy.exit("X","L",profit=10,qty_percent=100)\n')
  r=self.run_code(code,data((FLAT,FLAT,(111,112,110,111))));st=r.simulation['statistics']
  self.assertEqual((st['cash_exact'],st['fills'],st['trades']),('1031',2,1));self.assertEqual(st['protections'],[])
  r=self.run_code(code.replace('max_position_size(3)','max_position_size(0.5)'),data((FLAT,FLAT)))
  self.assertEqual((r.simulation['statistics']['fills'],r.simulation['statistics']['commission']),(0,'0'));self.assertEqual(r.simulation['statistics']['protections'],[])
 def test_reversal_shared_order_hits_count_before_reopening_and_charges_once(self):
  code=source(rules='strategy.risk.max_intraday_filled_orders(2)\n',body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=1)\nif bar_index==1\n    strategy.entry("S",strategy.short,qty=3)\n')
  r=self.run_code(code,data((FLAT,FLAT,(110,111,109,110))));st=r.simulation['statistics']
  self.assertEqual((st['fills'],st['orders'],st['commission'],st['cash_exact'],st['risk']['filled_orders']),(2,2,'2','1008',2));self.assertIsNone(r.simulation['position'])
 def test_partial_order_counts_once_per_day_but_flat_fee_once_across_days(self):
  code=source(rules='strategy.risk.max_intraday_filled_orders(10)\n',body='if bar_index==0\n    strategy.order("A",strategy.long,qty=1,limit=100)\nif bar_index==1\n    strategy.order("A",strategy.long,qty=1,limit=110)\nif bar_index==2\n    strategy.exit("X","A",profit=10,qty_percent=100)\n')
  market=data((FLAT,FLAT,(110,111,109,110),(111,112,110,111),(120,121,119,120)),[0,60000,120000,180000,DAY])
  d=self.driver(code,market);saved=[];d.run(pause_after=4,checkpoint=lambda x:saved.append(loads(canonical(x.checkpoint()))))
  # Inspect through the broker API as checkpoint envelope layout is independent.
  self.assertEqual(d.simulation.broker.risk.filled_orders,3);self.assertEqual(len(d.simulation.broker.risk.seen_orders),1)
  resumed=self.driver(code,market);resumed.restore(saved[0]);resumed.run();r=resumed.finish(data_identity(market));st=r.simulation['statistics']
  self.assertEqual((st['fills'],st['commission'],st['risk']['filled_orders'],st['risk']['retired_orders']),(4,'3',1,1))
 def test_deferred_liquidation_survives_day_reset_before_new_entries(self):
  code=source(rules='strategy.risk.max_intraday_loss(20,strategy.cash)\n',body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=2)\n')
  market=data((FLAT,(100,101,89,90),(80,81,79,80)),[DAY-21600000,DAY-10800000,DAY],timeframe='180')
  first=self.driver(code,market);saved=[];first.run(pause_after=2,checkpoint=lambda x:saved.append(loads(canonical(x.checkpoint()))))
  self.assertTrue(first.simulation.broker.risk.needs_close)
  second=self.driver(code,market);second.restore(saved[0]);second.run();r=second.finish(data_identity(market))
  self.assertEqual((r.simulation['statistics']['cash_exact'],r.simulation['statistics']['risk']['filled_orders']),('958',1));self.assertFalse(r.simulation['statistics']['risk']['blocked'])
 def test_percentage_insolvency_is_permanent_across_period_resets(self):
  code=source(rules='strategy.risk.max_intraday_loss(100,strategy.percent_of_equity)\n',body='if bar_index==0 or bar_index==3\n    strategy.entry("L",strategy.long,qty=10)\n')
  market=data((FLAT,(100,101,0.5,1),(.01,.02,.005,.01),FLAT),[0,60000,120000,DAY])
  r=self.run_code(code,market,EconomicsSettings('0.01','1'));st=r.simulation['statistics']
  self.assertEqual(st['cash_exact'],'-1.9');self.assertTrue(st['risk']['halt']['permanent']);self.assertEqual(st['fills'],2)
 def test_stop_priority_is_independent_of_declaration_order_and_zero_is_inclusive(self):
  rules='strategy.risk.max_intraday_loss(1,strategy.cash)\nstrategy.risk.max_drawdown(1,strategy.cash)\n'
  r=self.run_code(source(rules=rules,body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=1)\n'),data((FLAT,FLAT)))
  self.assertEqual(r.simulation['statistics']['risk']['halt']['rule'],'max_drawdown')
  r=self.run_code(source(rules='strategy.risk.max_intraday_filled_orders(0)\n'),data((FLAT,FLAT)))
  self.assertEqual(r.simulation['statistics']['fills'],0);self.assertEqual(r.simulation['statistics']['risk']['halt']['bar_index'],0)
 def test_losing_days_include_fees_zero_days_reset_and_calendar_gaps_are_not_fabricated(self):
  code=source(family='lifetime',manual='1');market=bars('lifetime',3)
  for row in market['bars']:row.update(open=100.,high=101.,low=99.,close=100.)
  for i,row in enumerate(market['bars']):row['time']=(i//8)*3*DAY+(i%8)*10800000
  r=self.run_code(code,from_payload(market));st=r.simulation['statistics'];self.assertEqual((st['cash_exact'],st['risk']['halt']['rule'],st['risk']['halt']['bar_index']),('996','max_cons_loss_days',16))
  code=source(rules='strategy.risk.max_cons_loss_days(2)\n',body='if bar_index==0 or bar_index==16\n    strategy.entry("L",strategy.long,qty=1)\nif bar_index==1 or bar_index==17\n    strategy.close("L")\n')
  r=self.run_code(code,from_payload(market));self.assertIsNone(r.simulation['statistics']['risk']['halt'])
 def test_calendar_bar_periods_and_consecutive_days_require_daily_or_lower(self):
  from packages.contracts.calendar import shift_open
  for tf,start in (('D',0),('W',345600000),('M',0)):
   market=data((FLAT,)*4,[shift_open(start,tf,i) for i in range(4)],tf)
   code=source(rules='strategy.risk.max_intraday_filled_orders(1)\n',body='strategy.entry("L",strategy.long,qty=1)\n')
   r=self.run_code(code,market);st=r.simulation['statistics'];self.assertEqual((st['fills'],st['commission']),(4,'4'))
   self.assertEqual(st['risk']['period_kind'],'utc_day' if tf=='D' else 'chart_bar')
   if tf!='D':
    with self.assertRaises(OpenPineError) as ctx:self.run_code(source(family='lifetime'),market)
    self.assertEqual(ctx.exception.diagnostic.code,'RISK_TIMEFRAME')
 def test_warmup_cannot_activate_zero_limit_or_modify_risk_peaks(self):
  code=source(rules='strategy.risk.max_drawdown(0,strategy.cash)\n');market=from_payload(bars('daily',2))
  window={'warmup_start_ms':0,'score_start_ms':DAY,'end_ms':2*DAY}
  first=self.driver(code,market,window);saved=[];first.run(pause_after=8,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
  state=first.simulation.broker.risk.checkpoint();self.assertIsNone(state['period']);self.assertEqual((state['peak'],state['trigger_count']),('1000',0))
  second=self.driver(code,market,window);second.restore(saved[0]);second.run();self.assertEqual(second.simulation.broker.risk.halt['bar_index'],8)
 def test_risk_checkpoint_is_atomic_and_rejects_counter_period_peak_and_pending_tampering(self):
  code=source(rules='strategy.risk.max_drawdown(5,strategy.cash)\n',body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=1)\n');market=data((FLAT,(100,101,89,90),(80,81,79,80)))
  first=self.driver(code,market);first.run(pause_after=2,checkpoint=lambda d:None);saved=loads(canonical(first.simulation.broker.checkpoint()));p=self.session.compile(code)
  from packages.simulation.broker.lots import LotBroker
  from packages.runtime.engine import program_hash
  from packages.contracts.strategy_ir import group_site_rules
  changes=[('peak','1'),('period',DAY),('observed_fills',0),('needs_close',False),('seen_orders',['x']),('retired_orders',0)]
  for field,value in changes:
   with self.subTest(field=field):
    bad=copy.deepcopy(saved);bad['risk'][field]=value;dest=LotBroker(first.simulation.broker.policy,program_hash(p));before=dest.statistics()
    with self.assertRaises(ValueError):dest.restore(bad,count=2,last_time=60000,sites=group_site_rules(p))
    self.assertEqual(dest.statistics(),before)
  dest=LotBroker(first.simulation.broker.policy,program_hash(p));dest.restore(saved,count=2,last_time=60000,sites=group_site_rules(p));saved['risk']['halt']['rule']='bad'
  self.assertEqual(dest.risk.halt['rule'],'max_drawdown')
 def test_scoped_dynamic_and_duplicate_rules_are_explicitly_rejected(self):
  for rule in ('if true\n    strategy.risk.max_position_size(2)\n','unused() =>\n    strategy.risk.max_position_size(2)\n    1\n','strategy.risk.max_position_size(2)\nstrategy.risk.max_position_size(3)\n'):
   with self.assertRaises(OpenPineError):self.session.compile(source(rules=rule))
 def test_named_contracts_thresholds_and_literal_alert_metadata_all_versions(self):
  for v in range(1,7):
   code=source(v,rules='strategy.risk.max_position_size(contracts=2)\nstrategy.risk.max_drawdown(value=0,type=strategy.cash,alert_message="Лимит")\n',body='')
   r=self.run_code(code,data((FLAT,FLAT)));self.assertEqual(r.simulation['statistics']['risk']['halt']['alert_message'],'Лимит');self.assertEqual(r.simulation['statistics']['fills'],0)
 def test_global_configuration_after_order_statements_is_active_from_first_bar(self):
  body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=1)\n'
  before=source(rules='strategy.risk.max_intraday_filled_orders(0)\n',body=body)
  after=source(rules='',body=body+'strategy.risk.max_intraday_filled_orders(0)\n')
  a=self.run_code(before,data((FLAT,FLAT)));b=self.run_code(after,data((FLAT,FLAT)))
  self.assertEqual(tuple(o.values for o in a.outputs),tuple(o.values for o in b.outputs));self.assertEqual(b.simulation['statistics']['fills'],0);self.assertEqual(b.simulation['statistics']['risk']['halt']['bar_index'],0)
 def test_snapshot_rules_bind_before_risk_policy_and_conflicts_are_rejected(self):
  from packages.marketdata.providers import input_snapshot
  from apps.openpine.bootstrap import local_manager
  import tempfile
  p=self.session.compile(source())
  with tempfile.TemporaryDirectory() as d,local_manager(d) as manager:
   key=manager.market.save(input_snapshot({'schema_version':'openpine.market-input.2','data':bars('daily',1),'environment':{'schema_version':'openpine.instrument-environment.1','instrument':{'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'RISK','type':'custom'}},'trading_rules':{'schema_version':'openpine.trading-rules.1','tick_size':'1','quantity_step':'1','minimum_quantity':'1','minimum_notional':'50'}}))
   # Store constructors are checked by the pipeline; source binding must agree.
   src=manager.market.source(key);policy=source_policy(p,src);self.assertEqual(policy.timeframe,'180');self.assertEqual(policy.settings.quantity_step,'1')
   with self.assertRaises(OpenPineError):source_policy(p,src,EconomicsSettings('1','0.1'))

if __name__=='__main__':unittest.main()
