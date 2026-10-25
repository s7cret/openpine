from copy import deepcopy
from dataclasses import asdict
import unittest
from packages.contracts.order_groups import OrderGroupPolicy,GroupEntryIntent,GroupExitIntent,CloseAllIntent
from packages.contracts.trading import BrokerPolicy,BrokerSettings
from packages.contracts.partial_exits import PartialCloseIntent,PartialProtectionIntent
from packages.simulation.broker.lots import LotBroker


def policy(**kw):return OrderGroupPolicy('1000','1','1',0,BrokerSettings(money_decimals=2),**kw)
def entry(name,qty,direction=1,kind='order',group='',mode='none',limit=None,stop=None):
    return GroupEntryIntent(name,kind,name,direction,str(qty),limit,stop,group,mode)
def exit(name,from_entry,qty,limit,group=''):
    return GroupExitIntent(name,'exit',from_entry,name,None,str(limit),str(qty),None,group)


class GroupBrokerTests(unittest.TestCase):
    def bar(self,b,i,intents=(),o=100,h=None,l=None,c=None):
        h=o if h is None else h;l=o if l is None else l;c=o if c is None else c
        b.begin(i,i*60000,o,c,high=h,low=l)
        return b.commit(list(intents),i,i*60000)

    def rules(self,*intents):
        result={}
        for intent in intents:
            name=getattr(intent,'oca_name','');kind=getattr(intent,'oca_type','reduce' if name else 'none')
            result[intent.call_site_id]={'command':intent.kind,'entry_id':getattr(intent,'entry_id',''),
                'exit_id':getattr(intent,'exit_id',''),'oca_name':name,'oca_type':kind}
        return result

    def restore(self,b,rules):
        state=b.checkpoint();new=LotBroker(b.policy,b.identity)
        new.restore(state,count=b.count,last_time=b.last_time,sites=rules)
        self.assertEqual(new.checkpoint(),state);return new

    def test_policy_closed_wire_and_separate_profile(self):
        p=policy(pyramiding=3,close_entries_rule='ANY')
        self.assertEqual(BrokerPolicy.from_payload(asdict(p)),p)
        with self.assertRaises(ValueError):BrokerPolicy.from_payload({**asdict(p),'future':1})
        with self.assertRaises(ValueError):policy(pyramiding=17)

    def test_net_reduce_cross_zero_and_close_all_exact_fees(self):
        b=LotBroker(policy(),'net')
        buy=entry('A',5);reduce=entry('R',2,-1);reverse=entry('S',8,-1);close=CloseAllIntent('close')
        self.bar(b,0,[buy]);self.bar(b,1,[reduce]);self.assertEqual(b.statistics()['position_size'],'5')
        self.bar(b,2,[reverse],o=110);self.assertEqual(b.statistics()['position_size'],'3')
        self.bar(b,3,[close],o=120);self.assertEqual(b.statistics()['position_size'],'-5')
        b=self.restore(b,self.rules(buy,reduce,reverse,close));events=self.bar(b,4,o=90)
        s=b.statistics();self.assertEqual((s['cash_exact'],s['equity_exact'],s['commission']),('1208.7','1208.7','21.3'))
        self.assertEqual((s['fills'],s['trades'],s['closed_lots'],s['closed_positions']),(5,3,2,2))
        self.assertEqual(s['order_groups'],[]);self.assertEqual(b.groups.records,{})

    def test_cancel_at_open_removes_precomputed_peer_execution(self):
        b=LotBroker(policy(),'cancel');a=entry('A',2,group='pair',mode='cancel');other=entry('B',3,-1,group='pair',mode='cancel')
        self.bar(b,0,[a,other]);b=self.restore(b,self.rules(a,other));events=self.bar(b,1)
        self.assertEqual(b.statistics()['position_size'],'2');self.assertEqual(b.fill_count,1)
        self.assertEqual([e['value']['reason'] for e in events if e['kind']=='cancelled'],['oca_cancel'])
        self.assertEqual(len([e for e in events if e['kind']=='oca_filled']),1)

    def test_reduce_at_open_uses_current_peer_quantity_and_cancels_zero(self):
        b=LotBroker(policy(),'reduce');orders=[entry(n,q,group='g',mode='reduce') for n,q in [('A',2),('B',5),('C',1)]]
        self.bar(b,0,orders);events=self.bar(b,1)
        self.assertEqual(b.statistics()['position_size'],'5')
        self.assertEqual([e['value']['quantity'] for e in events if e['kind']=='fill'],['2','3'])
        self.assertEqual(b.groups.records,{})

    def test_none_has_no_peer_effect_and_net_order_ignores_entry_pyramiding(self):
        b=LotBroker(policy(),'none');self.bar(b,0,[entry('A',2,group='g'),entry('B',3,-1,group='g')]);events=self.bar(b,1)
        self.assertEqual(b.statistics()['position_size'],'-1');self.assertEqual(b.fill_count,3)
        self.assertFalse(any(e['kind']=='oca_filled' for e in events))

    def test_activated_stop_limit_and_attached_reservation_survive_reduce(self):
        b=LotBroker(policy(pyramiding=3),'activation')
        a=entry('A',2,group='g',mode='reduce',limit='95');other=entry('B',5,group='g',mode='reduce',limit='103',stop='105')
        protection=exit('PB','B',5,140)
        self.bar(b,0,[a,other,protection]);self.bar(b,1,o=100,h=106,l=99,c=100)
        self.assertEqual(b.entries['B'].activated_bar,1)
        b=self.restore(b,self.rules(a,other,protection));self.bar(b,2,o=90,h=100,l=89,c=95)
        self.assertEqual(b.statistics()['position_size'],'5');self.assertEqual([p['quantity'] for p in b.ledger.ordered()],['2','3'])
        self.assertEqual(sum(int(t.quantity) for t in b.protections['PB'].targets),3)
        self.restore(b,self.rules(a,other,protection))

    def test_exit_reduce_shrinks_exact_targets_and_preserves_fee_residue(self):
        b=LotBroker(policy(),'exits');a=entry('A',2);other=entry('B',3);pa=exit('PA','A',2,110,'exits');pb=exit('PB','B',3,120,'exits');close=CloseAllIntent('close')
        self.bar(b,0,[a,other]);self.bar(b,1,[pa,pb]);self.bar(b,2,o=100,h=111,l=99,c=100)
        self.assertEqual([t.quantity for t in b.protections['PB'].targets],['1'])
        self.assertEqual(b.groups.checkpoint()['members'][0]['original_quantity'],'3')
        b=self.restore(b,self.rules(a,other,pa,pb,close));self.bar(b,3,[close],o=100,h=121,l=99,c=100);self.bar(b,4)
        self.assertEqual((b.statistics()['cash_exact'],b.statistics()['commission']),('1029.6','10.4'))

    def test_close_all_freezes_old_lots_and_does_not_close_future_fill(self):
        b=LotBroker(policy(close_entries_rule='ANY'),'close-all');a=entry('A',2);other=entry('B',3);future=entry('C',4)
        partial=PartialCloseIntent('part','close','B',0,'1',None);close=CloseAllIntent('close')
        self.bar(b,0,[a,other]);self.bar(b,1,[future,partial,close]);self.assertEqual(sum(int(t.quantity) for p in b.pending for t in p.targets),5)
        b=self.restore(b,self.rules(a,other,future,partial,close));self.bar(b,2)
        self.assertEqual([(p['entry_id'],p['quantity']) for p in b.ledger.ordered()],[('C','4')])

    def test_replacing_entry_with_net_order_releases_old_group_and_protection(self):
        b=LotBroker(policy(),'replacement');first=entry('A',3,kind='entry',group='first',mode='cancel',limit='95');pa=exit('P','A',3,130)
        self.bar(b,0,[first,pa]);self.bar(b,1,[entry('A',2,-1,group='second',mode='reduce',stop='105')])
        self.assertEqual(b.protections,{});self.assertEqual(len(b.groups.records),1)
        self.assertEqual(b.groups.statistics()[0]['name'],'second')

    def test_type_conflict_and_checkpoint_tampering_reject(self):
        b=LotBroker(policy(),'state');a=entry('A',2,group='g',mode='cancel',limit='95');self.bar(b,0,[a]);rules=self.rules(a)
        original=b.checkpoint()
        for key,value in [('command','entry'),('name','other'),('remaining_quantity','1'),('original_quantity','1')]:
            state=deepcopy(original);state['groups']['members'][0][key]=value;new=LotBroker(b.policy,b.identity)
            with self.assertRaises(ValueError):new.restore(state,count=1,last_time=0,sites=rules)
            self.assertEqual(new.phase,'ready');self.assertEqual(new.groups.records,{})
        with self.assertRaises(ValueError):self.bar(b,1,[entry('B',2,group='g',mode='reduce',limit='95')])

    def test_hard_limit_sixteen_net_lots_and_restore_with_zero_pyramiding(self):
        b=LotBroker(policy(),'capacity');order=entry('A',1)
        self.bar(b,0,[order])
        for i in range(1,18):self.bar(b,i,[order] if i<17 else [])
        self.assertEqual(len(b.ledger.lots),16);self.assertEqual(b.statistics()['position_size'],'16')
        self.restore(b,self.rules(order))

    def test_intrabar_cancel_applies_before_other_touched_candidate(self):
        b=LotBroker(policy(),'intrabar');a=entry('A',2,group='g',mode='cancel',stop='105');other=entry('B',3,-1,group='g',mode='cancel',stop='95')
        self.bar(b,0,[a,other]);events=self.bar(b,1,o=100,h=110,l=90,c=100)
        self.assertEqual(b.fill_count,1);self.assertEqual(b.statistics()['position_size'],'2');self.assertEqual(b.entries,{})

    def test_reduce_does_not_resurrect_a_cancelled_pending_protection_peer(self):
        b=LotBroker(policy(),'cascade');a=entry('A',2,group='g',mode='reduce');other=entry('B',1,group='g',mode='reduce',limit='90')
        protection=exit('PB','B',1,130,'g')
        self.bar(b,0,[a,other,protection]);self.bar(b,1)
        self.assertEqual(b.entries,{});self.assertEqual(b.protections,{});self.assertEqual(b.groups.records,{})

    def test_entry_reversal_reduces_group_by_both_fill_legs(self):
        b=LotBroker(policy(pyramiding=2),'turnover');self.bar(b,0,[entry('A',3)]);self.bar(b,1,[
            entry('R',2,-1,kind='entry',group='g',mode='reduce'),entry('P',7,group='g',mode='reduce',limit='90')])
        self.bar(b,2)
        self.assertEqual(b.statistics()['position_size'],'-2');self.assertEqual(b.entries['P'].intent.quantity,'2')


if __name__=='__main__':unittest.main()
