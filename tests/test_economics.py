"""Independent economic quantities, fee allocation and atomic restore boundaries."""
import copy,unittest
from dataclasses import asdict
from decimal import Decimal
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.economics import EconomicsPolicy,EconomicsSettings,CommissionPolicy
from packages.contracts.trading import BrokerSettings,BrokerPolicy
from packages.simulation.broker.economics import QuantityResolver
from packages.execution.session import Session
from packages.contracts.strategy_ir import broker_policy,group_site_rules
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.marketdata import from_payload
from packages.contracts.wire import canonical,loads

def data(rows=((100,101,99,100),(120,121,119,120),(130,131,129,130),(110,111,109,110))):
 return from_payload({'schema_version':'openpine.historical-bars.2','context_id':'economic','symbol':'TEST:ECON','timeframe':'1',
  'bars':[{'time':i*60000,'open':o,'high':h,'low':l,'close':c,'volume':1} for i,(o,h,l,c) in enumerate(rows)]})
def source(v=6,mode='cash',fee='percent',value=1,body=None):
 direction='true' if v==1 else 'strategy.long'
 return f'//@version={v}\nstrategy("Economics",default_qty_type=strategy.{mode},default_qty_value=100,initial_capital=1000,commission_type=strategy.commission.{fee},commission_value={value},close_entries_rule="ANY")\n'+(body or f'if bar_index==0\n    strategy.entry("L",{direction})\nif bar_index==2\n    strategy.close("L")\n')+'plot(strategy.position_size)\nplot(strategy.equity)\n'

class EconomicTests(unittest.TestCase):
 @classmethod
 def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
 def test_closed_policy_payloads_legacy_settings_and_minima(self):
  p=EconomicsPolicy('1000','100','0',0,EconomicsSettings('1','0.1',8,minimum_quantity='0.2',minimum_notional='10'),quantity_mode='cash',commission=CommissionPolicy('cash_per_order','5'))
  self.assertEqual(BrokerPolicy.from_payload(asdict(p)),p)
  self.assertEqual(asdict(BrokerSettings()),{'tick_size':None,'quantity_step':'0.00000001','money_decimals':8,'schema_version':'openpine.broker-settings.1'})
  for settings in ({'minimum_quantity':'0.11'},{'minimum_notional':'-1'},{'schema_version':'bad'}):
   with self.assertRaises(ValueError):EconomicsSettings(quantity_step='0.1',**settings)
  for mode,value in [('percent_of_equity','101'),('fixed','0'),('cash','-1')]:
   with self.assertRaises(ValueError):EconomicsPolicy('1000',value,'0',0,quantity_mode=mode)
 def test_cash_percent_and_explicit_qty_floor_only_derived_units(self):
  for mode,budget in [('cash',Decimal(100)),('percent_of_equity',Decimal(1000))]:
   q=QuantityResolver(EconomicsPolicy('1000','100','0',0,BrokerSettings(quantity_step='0.3'),quantity_mode=mode))
   r=q.resolve(None,'120',Decimal(1000));self.assertEqual(Decimal(r['quantity']),Decimal('0.6') if mode=='cash' else Decimal('8.1'));self.assertEqual(Decimal(r['budget']),budget)
   self.assertEqual(q.resolve('1.2','120',Decimal(1000))['mode'],'explicit')
   with self.assertRaises(ValueError):q.resolve('1','120',Decimal(1000))
 def test_minimum_unknown_zero_and_rounding_reasons(self):
  p=EconomicsPolicy('1000','1','0',0,EconomicsSettings(quantity_step='1',minimum_quantity='2',minimum_notional='300'),quantity_mode='cash')
  self.assertEqual(QuantityResolver(p).resolve(None,'100',Decimal(1000))['reason'],'zero_after_rounding')
  q=QuantityResolver(p);self.assertEqual(q.resolve('1','100',Decimal(1000))['reason'],'below_minimum_quantity');self.assertEqual(q.resolve('2','100',Decimal(1000))['reason'],'below_minimum_notional')
 def test_all_versions_cash_sizing_frozen_before_gap_and_three_commissions(self):
  for v in range(1,7):
   for fee,value,fees,equity in [('percent',1,'2.3','987.7'),('cash_per_contract',2,'4','986'),('cash_per_order',5,'10','980')]:
    with self.subTest(v=v,fee=fee):
     p,r=self.session.run(source(v,fee=fee,value=value),data=data());sim=r.simulation
     self.assertEqual(sim['schema_version'],'openpine.simulation.8');self.assertEqual(sim['statistics']['commission'],fees)
     self.assertEqual(sim['statistics']['equity_exact'],equity);self.assertEqual(sim['statistics']['gross_realized_exact'],'-10')
     size=next(e['value'] for e in sim['events'] if e['kind']=='quantity_resolved');self.assertEqual((size['quantity'],size['reference_price'],size['budget']),('1','100','100'))
     fills=[e['value'] for e in sim['events'] if e['kind']=='fill'];self.assertEqual([(f['quantity'],f['price']) for f in fills],[('1','120'),('1','110')])
 def test_every_boundary_preserves_pending_lots_fees_and_frozen_gap_size(self):
  for fee in ('percent','cash_per_contract','cash_per_order'):
   p=self.session.compile(source(fee=fee,value=2));rt=self.session.validate_program(p);policy=broker_policy(p);market=data();expected=self.session.execute(p,data=market)
   for cut in range(1,5):
    def driver():return HistoricalDriver(rt.prepare(p),MemorySource(market),simulation_policy=policy)
    first=driver();saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
    self.assertEqual(saved[0]['simulation']['broker']['schema_version'],'openpine.broker-state.8')
    resumed=driver();resumed.restore(saved[0]);resumed.run()
    from packages.marketdata import data_identity
    self.assertEqual(resumed.finish(data_identity(market)),expected)
 def test_flat_fee_is_once_for_reversal_order_close_and_open_fills(self):
  body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=2)\nif bar_index==1\n    strategy.entry("S",strategy.short,qty=3)\n'
  _,r=self.session.run(source(fee='cash_per_order',value=5,body=body),data=data());s=r.simulation['statistics']
  self.assertEqual((s['fills'],s['commission'],s['entry_commission'],s['exit_commission']), (3,'10','5','5'))
  self.assertEqual(s['lots'][0]['original_commission'],'0')
 def test_economic_fee_state_tamper_fails_before_destination_changes(self):
  p=self.session.compile(source(fee='cash_per_order',value=5));rt=self.session.validate_program(p);policy=broker_policy(p);first=HistoricalDriver(rt.prepare(p),MemorySource(data()),simulation_policy=policy);saved=[]
  first.run(pause_after=2,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))));state=saved[0]['simulation']['broker']
  from packages.simulation.broker.lots import LotBroker
  b=LotBroker(policy,state['identity']);bad=copy.deepcopy(state);bad['economics']['commission']['charged_count']=0
  with self.assertRaises(ValueError):b.restore(bad,count=2,last_time=60000,sites=group_site_rules(p))
  self.assertEqual(b.phase,'ready');self.assertEqual(b.cash,Decimal(1000));self.assertEqual(b.ledger.lots,{})
 def test_legacy_explicit_fixed_and_percent_keep_original_broker(self):
  p=self.session.compile('//@version=6\nstrategy("Legacy",default_qty_type=strategy.fixed)\nstrategy.entry("L",strategy.long)')
  self.assertEqual(broker_policy(p).profile_id,'openpine.market-broker.1')
 def test_snapshot_rules_pin_step_minima_and_reject_explicit_conflicts(self):
  from packages.execution.environment import source_policy
  from packages.marketdata.providers import input_snapshot
  from tools.specification import ROOT,read_json
  p=self.session.compile(source());memory=MemorySource(data())
  rules={'schema_version':'openpine.trading-rules.1','tick_size':'1','quantity_step':'1','minimum_quantity':'2','minimum_notional':'150'}
  memory.manifest={'provenance':{'trading_rules':rules,'environment':{'schema_version':'openpine.instrument-environment.1','instrument':{'mintick':'1','pointvalue':'1','currency':'USD','basecurrency':'ECON','type':'custom'}}}}
  selected=source_policy(p,memory);self.assertEqual(selected.settings,EconomicsSettings('1','1',minimum_quantity='2',minimum_notional='150'))
  from packages.contracts import OpenPineError
  with self.assertRaises(OpenPineError):source_policy(p,memory,BrokerSettings(quantity_step='0.1'))
  with self.assertRaises(OpenPineError):source_policy(p,memory,EconomicsSettings('1','1',minimum_quantity='3'))
 def test_minimum_fill_gap_rejects_parent_and_cannot_resurrect_protection(self):
  s=source(body='if bar_index==0\n    strategy.entry("L",strategy.long)\n    strategy.exit("X","L",profit=10)\n')
  _,r=self.session.run(s,data=data(((100,101,99,100),(40,41,39,40),(100,101,99,100))),broker_settings=EconomicsSettings('1','1',minimum_notional='50'))
  st=r.simulation['statistics'];self.assertEqual((st['fills'],st['entries'],st['protections'],st['exit_states']),(0,[],[],[]))
  self.assertTrue(any(e['kind']=='ignored' and e['value']['reason']=='below_minimum_notional' for e in r.simulation['events']))
 def test_percent_of_equity_uses_creation_mark_after_previous_fills_and_fees(self):
  q=QuantityResolver(EconomicsPolicy('1000','20','0',0,EconomicsSettings(quantity_step='1'),quantity_mode='percent_of_equity'))
  self.assertEqual(q.resolve(None,'100',Decimal(1500))['quantity'],'3')
  self.assertEqual(q.resolve(None,'100',Decimal(-1))['status'],'skipped')
 def test_explicit_quantity_overrides_unaligned_unused_fixed_default(self):
  p=EconomicsPolicy('1000','1','0',0,BrokerSettings(quantity_step='0.3'))
  self.assertEqual(QuantityResolver(p).resolve('1.2','100',Decimal(1000))['quantity'],'1.2')
  with self.assertRaises(ValueError):QuantityResolver(p).resolve(None,'100',Decimal(1000))
 def test_one_flat_fee_survives_partial_order_across_different_target_levels(self):
  body='if bar_index==0\n    strategy.entry("L",strategy.long,qty=1)\nif bar_index==1\n    strategy.entry("L",strategy.long,qty=1)\nif bar_index==2\n    strategy.exit("X","L",profit=10)\n'
  code=source(mode='fixed',fee='cash_per_order',value=2,body=body).replace('close_entries_rule=', 'pyramiding=2,close_entries_rule=')
  market=data(((100,101,99,100),(100,101,99,100),(110,111,109,110),(111,112,110,111),(119,121,118,120)))
  p=self.session.compile(code);rt=self.session.validate_program(p);policy=broker_policy(p,BrokerSettings(tick_size='1'));baseline=self.session.execute(p,data=market,broker_settings=policy.settings)
  self.assertEqual((baseline.simulation['statistics']['commission'],baseline.simulation['statistics']['equity_exact']),('6','1015'))
  first=HistoricalDriver(rt.prepare(p),MemorySource(market),simulation_policy=policy);saved=[];first.run(pause_after=4,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
  checkpoint=saved[0];fee=checkpoint['simulation']['broker']['economics']['commission'];self.assertEqual((fee['charged_count'],fee['retired_count'],len(fee['paid_order_ids'])),(3,2,1))
  resumed=HistoricalDriver(rt.prepare(p),MemorySource(market),simulation_policy=policy);resumed.restore(checkpoint);resumed.run()
  from packages.marketdata import data_identity
  self.assertEqual(resumed.finish(data_identity(market)),baseline)
  bad=copy.deepcopy(checkpoint);bad['simulation']['broker']['economics']['commission']['paid_order_ids']=[]
  other=HistoricalDriver(rt.prepare(p),MemorySource(market),simulation_policy=policy)
  with self.assertRaises(ValueError):other.restore(bad)
 def test_economic_strategy_can_run_without_any_plot(self):
  code='//@version=6\nstrategy("No plots",default_qty_type=strategy.cash,default_qty_value=100)\nif bar_index==0\n    strategy.entry("L",strategy.long)'
  _,r=self.session.run(code,data=data());self.assertEqual(r.outputs,());self.assertEqual(r.simulation['statistics']['position_size'],'1')
 def test_skipped_reissue_preserves_previously_accepted_pending_quantity(self):
  code=source(body='if bar_index==0 or bar_index==1\n    strategy.entry("L",strategy.long,limit=90)\n')
  market=data(((100,101,99,100),(200,201,199,200),(100,101,89,100)))
  _,r=self.session.run(code,data=market,broker_settings=EconomicsSettings('1','1'))
  stats=r.simulation['statistics'];self.assertEqual((stats['orders'],stats['fills'],stats['position_size']),(1,1,'1'))
  decisions=[e['value'] for e in r.simulation['events'] if e['kind']=='quantity_resolved'];self.assertEqual([v['status'] for v in decisions],['resolved','skipped'])

if __name__=='__main__':unittest.main()
