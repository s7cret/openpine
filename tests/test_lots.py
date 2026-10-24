"""Independent lot arithmetic, identities, reservation lifetime and atomic readers."""
import copy,json,unittest
from decimal import Decimal,localcontext
from dataclasses import asdict,replace
from packages.contracts.lots import LotBrokerPolicy,LotTarget
from packages.contracts.trading import BrokerSettings,BrokerPolicy,OrderIntent
from packages.contracts.pending_entries import EntryIntent
from packages.contracts.partial_exits import PartialCloseIntent,PartialProtectionIntent
from packages.contracts.protection import ProtectionIntent
from packages.simulation.broker.lots import LotBroker

def policy(rule='ANY',cap=3,**kwargs):return LotBrokerPolicy('1000','1','1',0,kwargs.pop('settings',BrokerSettings()),pyramiding=cap,close_entries_rule=rule,**kwargs)
def entry(name='L',qty='1',direction=1,**kwargs):return EntryIntent('site','entry',name,direction,qty,**kwargs)
def close(name='L',qty=None,percent=None):return PartialCloseIntent('site','close',name,0,qty,percent)
def exit(name='A',source='L',qty=None,percent=None,stop='90',limit='130'):return PartialProtectionIntent('site','exit',source,name,stop,limit,qty,percent)
def step(b,price='100',intents=(),high=None,low=None):
 i=b.count;b.begin(i,i*60000,price,price,high=high or price,low=low or price);return b.commit(list(intents),i,i*60000)
def wire(b):return json.loads(json.dumps(b.checkpoint()))
def restore(b):
 n=LotBroker(b.policy,b.identity);n.restore(wire(b),count=b.count,last_time=b.last_time,sites={'site'});return n

class LotBrokerTests(unittest.TestCase):
 def test_independent_two_lot_partial_accounting_and_fee_residues(self):
  b=LotBroker(policy(),'arithmetic');step(b,intents=[entry(qty='2')]);step(b,intents=[entry(qty='3')]);step(b,'110',[close(qty='4')])
  self.assertEqual(b.views(),{'position_size':5.,'position_avg_price':106.,'equity':1014.7})
  events=step(b,'120',[close()]);trades=[e['value'] for e in events if e['kind']=='trade']
  self.assertEqual([(t['entry_price'],t['quantity'],t['entry_commission'],t['exit_commission']) for t in trades],[('100','2','2','2.4'),('110','2','2.2','2.4')])
  self.assertEqual(b.statistics()['equity_exact'],'1059.9');events+=step(b,'90')
  self.assertEqual({k:b.statistics()[k] for k in ('cash_exact','commission','fills','trades','closed_lots','closed_positions')},{'cash_exact':'1029','commission':'11','fills':4,'trades':3,'closed_lots':2,'closed_positions':1})
  self.assertEqual(sum(Decimal(e['value']['net_profit']) for e in events if e['kind']=='trade'),Decimal(29))
  self.assertEqual(b.ledger.entry_fees,b.ledger.allocated_entry_fees)

 def test_fifo_and_any_close_different_exact_lots(self):
  for rule,expected in [('FIFO','A'),('ANY','B')]:
   with self.subTest(rule=rule):
    b=LotBroker(policy(rule),'selection');step(b,intents=[entry('A','2')]);step(b,intents=[entry('B','3')]);step(b,'110',[close('B','2')]);events=step(b,'120')
    trades=[e['value'] for e in events if e['kind']=='trade'];self.assertEqual([t['entry_id'] for t in trades],[expected]);self.assertEqual(trades[0]['requested_entry_id'],'B')
    self.assertEqual(restore(b).checkpoint(),b.checkpoint())

 def test_one_exit_fill_fee_allocated_cumulatively_across_three_lots(self):
  b=LotBroker(policy(settings=BrokerSettings(money_decimals=2)),'rounding')
  step(b,'10',[entry()]);step(b,'10',[entry()]);step(b,'10',[entry()]);step(b,'10',[close()]);events=step(b,'0.5')
  fills=[e['value'] for e in events if e['kind']=='fill'];trades=[e['value'] for e in events if e['kind']=='trade']
  self.assertEqual(len(fills),1);self.assertEqual(fills[0]['commission'],'0.02');self.assertEqual([t['exit_commission'] for t in trades],['0.01','0','0.01'])
  self.assertEqual([t['position_closed'] for t in trades],[False,False,True]);self.assertEqual(b.statistics()['closed_lots'],3)

 def test_capacity_checked_at_fill_and_same_id_keeps_previous_lots(self):
  b=LotBroker(policy(cap=2),'capacity');step(b,intents=[entry()]);step(b,intents=[entry()]);step(b,intents=[entry()]);events=step(b)
  self.assertEqual(b.ledger.quantity(),Decimal(2));self.assertEqual(len(b.ledger.lots),2)
  self.assertTrue(any(e['kind']=='ignored' and e['value']['reason']=='pyramiding_limit' for e in events));self.assertEqual(restore(b).checkpoint(),b.checkpoint())

 def test_reverse_closes_all_old_lots_then_opens_requested_short(self):
  b=LotBroker(policy(),'reverse');step(b,intents=[entry('A','2')]);step(b,intents=[entry('B','3')]);step(b,'110',[entry('S','4',-1),exit('S1','S',stop='140',limit='70')]);events=step(b,'120')
  self.assertEqual([(e['value']['direction'],e['value']['quantity']) for e in events if e['kind']=='fill'],[(-1,'5'),(-1,'4')])
  self.assertEqual([p['entry_id'] for p in b.ledger.ordered()],['S']);self.assertEqual(b.views()['position_size'],-4.)
  self.assertTrue(b.protections);self.assertEqual(restore(b).checkpoint(),b.checkpoint())

 def test_reservations_never_double_book_and_frozen_targets_do_not_rebind(self):
  b=LotBroker(policy(),'reservations');step(b,intents=[entry(qty='2')]);step(b,intents=[entry(qty='3')]);step(b,'110',[exit(qty='4'),exit('B',qty='4'),close(qty='2')])
  self.assertEqual([sum(Decimal(t.quantity) for t in o.targets) for o in b.protections.values()],[Decimal(4),Decimal(1)])
  step(b,'115');self.assertEqual(sum(Decimal(t.quantity) for o in b.protections.values() for t in o.targets),Decimal(3))
  self.assertEqual(restore(b).checkpoint(),b.checkpoint());step(b,'130');self.assertFalse(b.ledger.lots)

 def test_pending_protection_fifo_binding_happens_once_on_fill(self):
  for rule in ('FIFO','ANY'):
   b=LotBroker(policy(rule),'activation');step(b,intents=[entry('A','2')]);step(b,intents=[entry('B','2',limit='95'),exit('P','B',stop='80',limit='140')]);step(b,'95')
   target=b.protections['P'].targets[0].target_id;lot=b.ledger.lots[target]
   self.assertEqual(lot['entry_id'],'A' if rule=='FIFO' else 'B');self.assertEqual(restore(b).checkpoint(),b.checkpoint())

 def test_cancel_and_replace_pending_preserve_open_lots_and_remove_old_targets(self):
  b=LotBroker(policy(),'cancel');step(b,intents=[entry('A')]);step(b,intents=[entry('B',limit='80'),exit('P','B',stop='70',limit='140')]);old=b.entries['B'].order_id
  step(b,intents=[entry('B',limit='75')]);self.assertFalse(b.protections);self.assertNotEqual(old,b.entries['B'].order_id)
  step(b,intents=[ProtectionIntent('site','cancel_all','','')]);self.assertFalse(b.entries);self.assertEqual(len(b.ledger.lots),1)

 def test_stop_limit_activation_and_intrabar_protection_defer(self):
  b=LotBroker(policy(),'stop-limit');step(b,intents=[entry(stop='105',limit='103'),exit(stop='90',limit='120')]);step(b,'100',high='110',low='95')
  self.assertFalse(b.ledger.lots);self.assertIsNotNone(b.entries['L'].activated_bar);b=restore(b)
  step(b,'104',high='130',low='80');self.assertEqual(len(b.ledger.lots),1);self.assertEqual(b.protections['A'].eligible_bar,3)
  self.assertEqual(restore(b).checkpoint(),b.checkpoint());step(b,'100',high='130',low='80');self.assertFalse(b.ledger.lots)

 def test_all_boundaries_restore_without_duplicate_fills(self):
  actions=[('100',[entry(qty='2')]),('100',[entry(qty='3')]),('110',[exit(qty='3'),close(qty='1')]),('120',[]),('130',[])]
  baseline=LotBroker(policy(),'all-cuts');expected=[]
  for price,intents in actions:expected+=step(baseline,price,intents)
  for cut in range(1,len(actions)+1):
   b=LotBroker(policy(),'all-cuts');actual=[]
   for price,intents in actions[:cut]:actual+=step(b,price,intents)
   b=restore(b)
   for price,intents in actions[cut:]:actual+=step(b,price,intents)
   self.assertEqual(actual,expected);self.assertEqual(b.checkpoint(),baseline.checkpoint())

 def test_fifo_market_closes_freeze_disjoint_allocations_in_source_order(self):
  b=LotBroker(policy('FIFO'),'two-closes');step(b,intents=[entry('A','2')]);step(b,intents=[entry('B','3')]);step(b,'110',[close('B','1')])
  step(b,'120',[close('A'),close('B')]);self.assertEqual([o.intent.quantity for o in b.pending],['1','3'])
  ids=[t.target_id for o in b.pending for t in o.targets];self.assertEqual(len(ids),len(set(ids)))
  b=restore(b);step(b,'110');self.assertFalse(b.ledger.lots);self.assertEqual(b.statistics()['cash_exact'],'1019.1')

 def test_tampered_identity_quantities_fees_and_targets_reject_atomically(self):
  b=LotBroker(policy(),'tamper');step(b,intents=[entry()]);step(b,intents=[entry(),exit()]);step(b,'110',[exit(qty='1')]);state=wire(b)
  edits=[lambda s:s['ledger']['lots'][0].update(fill_id='wrong'),lambda s:s['ledger']['lots'][0].update(quantity='9'),
   lambda s:s['ledger'].update(allocated_entry_fees='99'),lambda s:s.update(cash='1'),
   lambda s:s['protections'][0]['targets'][0].update(quantity='99'),lambda s:s['protections'][0].update(eligible_bar=99),
   lambda s:s.update(schema_version='openpine.broker-state.4'),lambda s:s.update(position=None),lambda s:s.update(event_count=0),lambda s:s.update(order_count=1000)]
  for edit in edits:
   bad=copy.deepcopy(state);edit(bad);dest=LotBroker(b.policy,b.identity);original=dest.ledger
   before=copy.deepcopy({k:v for k,v in dest.__dict__.items() if k!='ledger'});ledger_before=dest.ledger.checkpoint()
   with self.assertRaises(ValueError):dest.restore(bad,count=b.count,last_time=b.last_time,sites={'site'})
   self.assertEqual({k:v for k,v in dest.__dict__.items() if k!='ledger'},before);self.assertIs(dest.ledger,original);self.assertEqual(dest.ledger.checkpoint(),ledger_before)

 def test_large_notional_and_fee_state_do_not_use_default_decimal_precision(self):
  b=LotBroker(policy(),'large');n='1000000000000000000000000';step(b,n,[entry(qty=n)]);step(b,n,[close()])
  self.assertEqual(restore(b).checkpoint(),b.checkpoint());step(b,n)
  with localcontext() as c:
   c.prec=80;self.assertEqual(Decimal(b.statistics()['cash_exact']),Decimal(1000)-Decimal('2e46'))
  self.assertEqual(restore(b).checkpoint(),b.checkpoint())

 def test_policy_wire_roundtrip_and_closed_fields(self):
  for v in (0,1,2,16):
   p=policy(cap=v);self.assertEqual(BrokerPolicy.from_payload(asdict(p)),p)
  for change in ({'pyramiding':17},{'pyramiding':True},{'close_entries_rule':'LIFO'},{'profile_id':'openpine.market-broker.1'}):
   with self.assertRaises(ValueError):replace(policy(),**change)
  raw=asdict(policy());raw.pop('pyramiding')
  with self.assertRaises(ValueError):BrokerPolicy.from_payload(raw)

if __name__=='__main__':unittest.main()
