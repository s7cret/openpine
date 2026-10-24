"""Independent prices and lifecycle boundaries for persistent historical entries."""
import copy
from dataclasses import replace
import unittest
from packages.contracts.pending_entries import PROFILE,EntryIntent
from packages.contracts.partial_exits import PartialProtectionIntent,PartialCloseIntent
from packages.contracts.trading import BrokerPolicy,BrokerSettings,OrderIntent
from packages.simulation.broker.pending import PendingEntryBroker

def broker(fee='0',slip=0):return PendingEntryBroker(BrokerPolicy('1000','10',fee,slip,BrokerSettings('1','1',2),PROFILE),'fixture')
def entry(name='L',direction=1,qty='10',limit=None,stop=None):return EntryIntent('entry','entry',name,direction,qty,limit,stop)
def exit(name='A',qty='5',limit='110',stop='90',target='L'):return PartialProtectionIntent('exit','exit',target,name,stop,limit,qty,None)
def bar(b,index,intents=(),opening=100,high=101,low=99,close=100):
 b.begin(index,index*60000,opening,close,high=high,low=low);return b.commit(intents,index,index*60000)
def restored(b):
 value=copy.deepcopy(b.checkpoint());new=broker(b.policy.commission_percent,b.policy.slippage_ticks)
 new.restore(value,count=b.count,last_time=b.last_time,sites={'entry','exit','close','cancel'});return new

class PendingEntryTests(unittest.TestCase):
 def test_limit_never_fills_on_creation_and_improves_at_gap_open_without_slippage(self):
  b=broker(slip=2);bar(b,0,[entry(limit='95')],low=90)
  self.assertIsNone(b.position);self.assertEqual(len(b.entries),1)
  bar(b,1,opening=90,high=94,low=89,close=92)
  self.assertEqual((b.position['quantity'],b.position['price']),('10','90'))

 def test_limit_touch_and_short_limit_improvement(self):
  for direction,limit,opening,high,low,price in [(1,'95',100,101,94,'95'),(-1,'105',110,111,109,'110'),(-1,'105',100,106,99,'105')]:
   with self.subTest(direction=direction,price=price):
    b=broker(slip=2);bar(b,0,[entry(direction=direction,limit=limit)]);bar(b,1,opening=opening,high=high,low=low,close=opening)
    self.assertEqual(b.position['price'],price)

 def test_stop_touch_and_gaps_receive_directional_slippage(self):
  for direction,stop,opening,high,low,price in [(1,'105',100,106,99,'107'),(1,'105',110,111,109,'112'),(-1,'95',100,101,94,'93'),(-1,'95',90,91,89,'88')]:
   with self.subTest(direction=direction,price=price):
    b=broker(slip=2);bar(b,0,[entry(direction=direction,stop=stop)]);bar(b,1,opening=opening,high=high,low=low,close=opening)
    self.assertEqual(b.position['price'],price)

 def test_stop_limit_intrabar_activation_defers_even_if_limit_is_marketable(self):
  for limit in ('103','108'):
   b=broker();bar(b,0,[entry(limit=limit,stop='105')]);events=bar(b,1,high=106,low=100)
   self.assertIsNone(b.position);self.assertEqual(b.entries['L'].activated_bar,1)
   self.assertEqual([e['kind'] for e in events].count('entry_activated'),1)
   b=restored(b);bar(b,2,high=106,low=100)
   self.assertEqual(b.position['price'],'100')

 def test_stop_limit_open_activation_allows_later_limit_touch(self):
  b=broker(slip=2);bar(b,0,[entry(limit='108',stop='105')]);bar(b,1,opening=110,high=112,low=107,close=110)
  self.assertEqual(b.position['price'],'108')

 def test_short_stop_limit_activation_and_next_bar_limit(self):
  b=broker();bar(b,0,[entry(direction=-1,limit='97',stop='95')]);bar(b,1,high=100,low=94)
  self.assertIsNone(b.position);b=restored(b);bar(b,2,opening=96,high=98,low=95,close=97)
  self.assertEqual(b.position['price'],'97')

 def test_intrabar_entry_cannot_use_earlier_unknown_protective_touches(self):
  b=broker();bar(b,0,[entry(limit='95'),exit(qty='10')]);bar(b,1,high=111,low=94,close=100)
  self.assertEqual(b.position['quantity'],'10');self.assertEqual(b.trade_count,0)
  b=restored(b);bar(b,2,high=111,low=89)
  self.assertIsNone(b.position);self.assertEqual(b.statistics()['equity'],'950')

 def test_open_entry_activates_both_partial_exits_on_same_bar_and_allocates_fees(self):
  b=broker(fee='1');bar(b,0,[entry(limit='100'),exit(limit='110',stop='95'),exit('B',limit='120',stop='95')])
  bar(b,1,high=111,low=99,close=110);self.assertEqual(b.position['quantity'],'5')
  b=restored(b);bar(b,2,high=101,low=94,close=95)
  self.assertEqual((b.statistics()['equity'],b.trade_count,b.closed_positions),('1004.75',2,1))
  self.assertEqual((b.statistics()['entry_commission'],b.statistics()['exit_commission']),('10','10.25'))

 def test_protection_stays_bound_to_exact_unfilled_order_across_many_bars(self):
  b=broker();bar(b,0,[entry(limit='95'),exit()])
  target=b.entries['L'].order_id
  for i in range(1,5):bar(b,i);b=restored(b)
  self.assertEqual(b.protections['A'].target_id,target);self.assertIsNone(b.position)
  bar(b,5,low=94);self.assertEqual(b.protections['A'].target_id,b.position['fill_id'])

 def test_replacement_resets_stop_limit_and_cancels_old_attached_brackets(self):
  b=broker();bar(b,0,[entry(limit='103',stop='105'),exit()]);old=b.entries['L'].order_id
  bar(b,1,[entry(limit='102',stop='120')],high=106,low=100)
  self.assertNotEqual(b.entries['L'].order_id,old);self.assertIsNone(b.entries['L'].activated_bar);self.assertEqual(b.protections,{})
  bar(b,2,high=110,low=99);self.assertIsNone(b.position)

 def test_cancel_and_cancel_all_remove_persistent_targets_and_pending_closes(self):
  b=broker();bar(b,0,[entry(limit='95'),entry('S',-1,limit='105'),exit()])
  bar(b,1,[PartialProtectionIntent('cancel','cancel','','L')]);self.assertEqual(set(b.entries),{'S'});self.assertEqual(b.protections,{})
  bar(b,2,[PartialProtectionIntent('cancel','cancel_all','','')]);self.assertEqual(b.entries,{})
  bar(b,3,high=111,low=90);self.assertEqual(b.fill_count,0)

 def test_multiple_intrabar_candidates_defer_after_first_actual_entry(self):
  b=broker();bar(b,0,[entry(limit='95'),entry('S',-1,limit='105')]);events=bar(b,1,high=106,low=94)
  self.assertEqual(b.position['direction'],1);self.assertEqual(set(b.entries),{'S'})
  self.assertTrue(any(e['kind']=='entry_deferred' for e in events));bar(b,2,high=106,low=99)
  self.assertEqual((b.position['direction'],b.entry_count,b.closed_positions),(-1,2,1))

 def test_open_candidates_execute_in_stable_order_and_reversal_cancels_old_protection(self):
  b=broker();bar(b,0,[entry(limit='101'),exit(),entry('S',-1,limit='99')]);bar(b,1)
  self.assertEqual((b.position['entry_id'],b.fill_count,b.closed_positions),('S',3,1));self.assertEqual(b.protections,{})
  restored(b)

 def test_manual_partial_close_and_pending_reversal_never_close_new_fill(self):
  b=broker();bar(b,0,[entry(limit='101')]);bar(b,1,[entry('S',-1,stop='99'),PartialCloseIntent('close','close','L',0,'5',None)])
  bar(b,2,opening=98,high=100,low=97,close=98)
  self.assertEqual((b.position['entry_id'],b.position['quantity']),('S','10'));self.assertEqual(b.trade_count,1)

 def test_pending_entry_cap_and_invalid_levels_poison_without_checkpoint(self):
  b=broker();bar(b,0,[entry(str(i),limit='1') for i in range(16)]);b.begin(1,60000,100,100,high=101,low=99)
  with self.assertRaises(ValueError):b.commit([entry('overflow',limit='1')],1,60000)
  with self.assertRaises(ValueError):b.checkpoint()
  for bad in (replace(entry(),limit='0'),replace(entry(),stop='1.5'),replace(entry(),quantity='0')):
   b=broker();b.begin(0,0,100,100,high=101,low=99)
   with self.assertRaises(ValueError):b.commit([bad],0,0)
   self.assertEqual(b.phase,'failed')

 def test_closed_restore_rejects_activation_target_ids_and_overbooking_atomically(self):
  b=broker();bar(b,0,[entry(limit='103',stop='105'),exit()]);bar(b,1,high=106,low=100);base=b.checkpoint()
  mutations=[lambda s:s['entry_orders'][0].update(activated_bar=0),lambda s:s['entry_orders'][0].update(activation_basis='ticks'),
   lambda s:s['entry_orders'][0]['intent'].update(quantity='0'),lambda s:s['entry_orders'][0].update(order_id='f'*64),
   lambda s:s['protections'][0].update(quantity='11'),lambda s:s.update(schema_version='openpine.broker-state.3'),
   lambda s:s['entry_orders'].append(copy.deepcopy(s['entry_orders'][0]))]
  for mutate in mutations:
   state=copy.deepcopy(base);mutate(state);destination=broker()
   with self.assertRaises(ValueError):destination.restore(state,count=2,last_time=60000,sites={'entry','exit'})
   self.assertEqual((destination.phase,destination.entries,destination.position),('ready',{},None))

 def test_every_boundary_restores_same_orders_activations_and_events(self):
  commands=[(entry(limit='103',stop='105'),exit()),(),(),(PartialCloseIntent('close','close','L',0,'5',None),),()]
  prices=[(100,101,99,100),(100,106,100,102),(104,105,102,104),(104,105,103,104),(104,105,103,104)]
  continuous=broker(fee='1');resuming=broker(fee='1')
  for i,(intents,(o,h,l,c)) in enumerate(zip(commands,prices,strict=True)):
   self.assertEqual(bar(continuous,i,intents,o,h,l,c),bar(resuming,i,intents,o,h,l,c));resuming=restored(resuming)
   self.assertEqual(continuous.checkpoint(),resuming.checkpoint())

if __name__=='__main__':unittest.main()
