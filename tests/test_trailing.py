from copy import deepcopy
from dataclasses import asdict,replace
from decimal import Decimal
import unittest
from packages.contracts.trailing import TrailingPolicy,TrailingExitIntent,PARAMETERS
from packages.contracts.order_groups import GroupEntryIntent,GroupExitIntent,CloseAllIntent
from packages.contracts.trading import BrokerSettings,BrokerPolicy
from packages.contracts.partial_exits import PartialCloseIntent
from packages.simulation.broker.lots import LotBroker

def policy(v=6,**kw):return TrailingPolicy('1000','1','1',0,BrokerSettings(tick_size='1',money_decimals=2),close_entries_rule='ANY',language_version=v,**kw)
def entry(name='L',quantity=2,direction=1,**kw):return GroupEntryIntent('entry-'+name,'order',name,direction,str(quantity),**kw)
def exit(name='X',from_entry='L',**kw):return TrailingExitIntent('exit-'+name,'exit',from_entry,name,**{k:str(v) if v is not None and k!='oca_name' else v for k,v in kw.items()})

class TrailingTests(unittest.TestCase):
    def bar(self,b,i,intents=(),o=100,h=None,l=None,c=None):
        h=o if h is None else h;l=o if l is None else l;c=o if c is None else c
        b.begin(i,i*60000,o,c,high=h,low=l);return b.commit(list(intents),i,i*60000)

    def rules(self,*intents):
        rules={}
        for i in intents:
            name=getattr(i,'oca_name','');kind=getattr(i,'oca_type','reduce' if name else 'none')
            rule={'command':i.kind,'entry_id':getattr(i,'entry_id',''),'exit_id':getattr(i,'exit_id',''),'oca_name':name,'oca_type':kind}
            if i.kind=='exit':
                rule['extended']=type(i) is TrailingExitIntent
                rule['exit_price_arguments']={n:{'dynamic':False,'value':getattr(i,n,None)} for n in ('stop','limit',*PARAMETERS)}
            rules[i.call_site_id]=rule
        return rules

    def restore(self,b,*intents):
        state=b.checkpoint();new=LotBroker(b.policy,b.identity);new.restore(deepcopy(state),count=b.count,last_time=b.last_time,sites=self.rules(*intents))
        self.assertEqual(state,new.checkpoint());return new

    def test_policy_wire_requires_pinned_tick_and_real_version(self):
        p=policy();self.assertEqual(BrokerPolicy.from_payload(asdict(p)),p)
        for kwargs in ({'language_version':0},{'language_version':True},{'pyramiding':17}):
            with self.assertRaises(ValueError):replace(p,**kwargs)
        with self.assertRaises(ValueError):replace(p,settings=BrokerSettings())
        with self.assertRaises(ValueError):BrokerPolicy.from_payload({**asdict(p),'future':1})

    def test_parameter_pairs_have_distinct_v5_and_v6_outcomes(self):
        for v in range(1,7):
            b=LotBroker(policy(v),'pairs');a=entry();x=exit(profit=5,loss=3,limit=120,stop=90)
            self.bar(b,0,[a,x]);events=self.bar(b,1,o=100,h=106,l=99,c=103)
            if v==6:
                self.assertEqual([e['value']['price'] for e in events if e['kind']=='fill'],['100','105']);self.assertEqual(b.statistics()['cash_exact'],'1005.9')
            else:
                state=b.exits.statistics()[0]['targets'][0];self.assertEqual((state['stop'],state['limit']),('90','120'))
                b=self.restore(b,a,x);self.bar(b,2,o=110,h=121,l=109,c=115);self.assertEqual(b.statistics()['cash_exact'],'1035.6')

    def test_long_and_short_trailing_defer_intrabar_activation_and_ratchet(self):
        rows=[(100,101,99,100),(100,104,99,103),(103,110,102,108),(108,112,108,111),(110,111,108,109)]
        for direction in (1,-1):
            b=LotBroker(policy(),'trail'+str(direction));a=entry(direction=direction);x=exit(trail_points=5,trail_offset=3)
            for i,(o,h,l,c) in enumerate(rows):
                if direction==-1:o,h,l,c=200-o,200-l,200-h,200-c
                events=self.bar(b,i,[a,x] if i==0 else [],o=o,h=h,l=l,c=c)
                if i==2:
                    self.assertEqual(b.trade_count,0);st=b.exits.statistics()[0]['targets'][0];self.assertEqual(st['stop_eligible_bar'],3)
                if i==3:
                    st=b.exits.statistics()[0]['targets'][0];self.assertEqual(st['effective_stop'],'107' if direction==1 else '93');self.assertEqual(st['trail_stop'],'109' if direction==1 else '91')
                b=self.restore(b,a,x)
            self.assertEqual(b.statistics()['cash_exact'],'1013.82' if direction==1 else '1014.18')
            self.assertEqual((b.fill_count,b.trade_count,b.closed_positions),(2,1,1));self.assertEqual(b.exits.records,{})

    def test_known_open_can_activate_and_ratchet_before_intrabar_stop(self):
        b=LotBroker(policy(),'open');a=entry();x=exit(trail_price=105,trail_offset=3)
        self.bar(b,0,[a,x]);self.bar(b,1,o=100,h=104,l=99,c=103)
        events=self.bar(b,2,o=110,h=112,l=106,c=108)
        self.assertEqual([e['value']['price'] for e in events if e['kind']=='fill'],['107'])
        self.assertEqual([e['value']['basis'] for e in events if e['kind']=='trailing_activated'],['open'])
        self.assertEqual(b.statistics()['cash_exact'],'1009.86')

    def test_zero_distance_exit_and_zero_offset_are_explicit(self):
        b=LotBroker(policy(),'zero');a=entry();x=exit(profit=0,loss=0)
        self.bar(b,0,[a,x]);self.bar(b,1,o=100,h=101,l=99,c=100);self.assertEqual(b.statistics()['cash_exact'],'996')
        b=LotBroker(policy(),'zero-offset');x=exit(trail_points=0,trail_offset=0)
        self.bar(b,0,[a,x]);self.bar(b,1,o=100,h=101,l=99,c=100);self.assertEqual(b.statistics()['cash_exact'],'996')

    def test_same_price_configuration_reissue_and_quantity_change_preserve_extreme(self):
        b=LotBroker(policy(),'reissue');a=entry(quantity=4);x=exit(trail_points=5,trail_offset=3,quantity=4)
        self.bar(b,0,[a,x]);self.bar(b,1,o=100,h=104,l=99,c=103);self.bar(b,2,[x],o=103,h=110,l=102,c=108)
        st=b.exits.statistics()[0]['targets'][0];self.assertEqual((st['activated_bar'],st['extreme'],st['trail_stop']),(2,'110','107'))
        smaller=replace(x,quantity='2');self.bar(b,3,[smaller],o=108,h=112,l=108,c=111)
        st=b.exits.statistics()[0]['targets'][0];self.assertEqual((st['quantity'],st['activated_bar'],st['extreme']),('2',2,'112'))
        self.bar(b,4,o=110,h=111,l=108,c=109);self.assertEqual(b.statistics()['position_size'],'2');self.assertEqual(b.trade_count,1)

    def test_changed_price_configuration_resets_activation(self):
        b=LotBroker(policy(),'reset');a=entry();x=exit(trail_price=105,trail_offset=3)
        self.bar(b,0,[a,x]);self.bar(b,1,o=100,h=110,l=99,c=108)
        modified=replace(x,trail_price='120');self.bar(b,2,[modified],o=110,h=112,l=108,c=111)
        state=b.exits.statistics()[0]['targets'][0];self.assertEqual(state['activation'],'120');self.assertIsNone(state['activated_bar'])

    def test_relative_levels_use_each_exact_lot_instead_of_average_entry(self):
        b=LotBroker(policy(),'lots');a=entry();x=exit(profit=5)
        self.bar(b,0,[a]);self.bar(b,1,[a],o=100);self.bar(b,2,[x],o=110)
        states=b.exits.statistics()[0]['targets'];self.assertEqual([(s['entry_price'],s['limit']) for s in states],[('100','105'),('110','115')])
        b=self.restore(b,a,x);events=self.bar(b,3,o=110,h=116,l=109,c=115)
        self.assertEqual([e['value']['price'] for e in events if e['kind']=='fill'],['110','115'])
        self.assertEqual((b.trade_count,b.closed_positions),(2,1));self.assertEqual(b.groups.records,{})

    def test_equal_target_levels_batch_one_fill_and_one_fee(self):
        b=LotBroker(policy(),'batch');a=entry();x=exit(profit=5)
        self.bar(b,0,[a]);self.bar(b,1,[a]);self.bar(b,2,[x]);self.bar(b,3,o=100,h=106,l=99,c=103)
        self.assertEqual((b.fill_count,b.trade_count),(3,2));self.assertEqual(b.statistics()['cash_exact'],'1011.8')

    def test_oca_reduce_keeps_surviving_trailing_target_and_reserved_quantity(self):
        b=LotBroker(policy(),'oca');a=entry('A',1);other=entry('B',4)
        take=exit('PA','A',profit=10,oca_name='g');trail=exit('PB','B',trail_points=5,trail_offset=3,oca_name='g')
        self.bar(b,0,[a,other,take,trail]);self.bar(b,1,o=100,h=104,l=99,c=103)
        self.bar(b,2,o=103,h=110,l=102,c=108)
        s=b.exits.statistics()[0]['targets'][0];self.assertEqual((s['quantity'],s['extreme']),('3','110'));self.assertEqual(b.statistics()['position_size'],'4')
        b=self.restore(b,a,other,take,trail);self.bar(b,3,o=108,h=112,l=108,c=111);self.bar(b,4,o=110,h=111,l=108,c=109)
        self.assertEqual(b.statistics()['position_size'],'1');self.assertEqual(b.trade_count,2);self.assertEqual(b.exits.records,{})

    def test_pending_intrabar_fill_has_next_bar_protection_and_exact_fill_basis(self):
        b=LotBroker(policy(),'pending');a=entry(limit='95');x=exit(profit=5,loss=3)
        self.bar(b,0,[a,x]);self.assertEqual(b.exits.statistics()[0]['targets'][0]['status'],'pending_entry')
        self.bar(b,1,o=100,h=101,l=90,c=93);self.assertEqual(b.trade_count,0)
        st=b.exits.statistics()[0]['targets'][0];self.assertEqual((st['entry_price'],st['stop'],st['limit']),('95','92','100'))
        b=self.restore(b,a,x);self.bar(b,2,o=90,h=93,l=89,c=91);self.assertEqual(b.statistics()['cash_exact'],'986.3')

    def test_fixed_stop_wins_when_more_protective_than_trailing(self):
        b=LotBroker(policy(),'fixed');a=entry();x=exit(stop=98,trail_points=5,trail_offset=20)
        self.bar(b,0,[a,x]);self.bar(b,1,o=100,h=110,l=99,c=108)
        events=self.bar(b,2,o=100,h=101,l=97,c=99);self.assertEqual([e['value']['role'] for e in events if e['kind']=='protection_trigger'],['stop'])

    def test_cancel_parent_close_all_and_reversal_remove_target_state(self):
        from packages.contracts.partial_exits import PartialProtectionIntent
        b=LotBroker(policy(),'cancel');a=entry(limit='95');x=exit(trail_points=5,trail_offset=3)
        self.bar(b,0,[a,x]);self.bar(b,1,[PartialProtectionIntent('cancel','cancel','',exit_id='L')]);self.assertEqual(b.exits.records,{})
        b=LotBroker(policy(),'close');a=entry();close=CloseAllIntent('close')
        self.bar(b,0,[a,x]);self.bar(b,1,[close],o=100,h=104,l=99,c=103);self.bar(b,2);self.assertEqual(b.exits.records,{})

    def test_invalid_configuration_and_unaligned_observations_fail(self):
        for kw in ({'profit':-1},{'loss':.5},{'trail_points':3},{'trail_offset':2},{'trail_price':0},{'trail_points':2,'trail_offset':-1}):
            b=LotBroker(policy(),'invalid')
            with self.subTest(kw=kw),self.assertRaises(ValueError):b.validate_intent(exit(**kw))
        b=LotBroker(policy(),'unaligned');a=entry();x=exit(trail_points=5,trail_offset=3)
        self.bar(b,0,[a,x]);self.bar(b,1,o=100,h=104,l=99,c=103)
        with self.assertRaises(ValueError):self.bar(b,2,o=103,h=110.5,l=102,c=108)

    def test_checkpoint_rejects_forged_configuration_state_or_membership_atomically(self):
        b=LotBroker(policy(),'state');a=entry();x=exit(trail_points=5,trail_offset=3)
        self.bar(b,0,[a,x]);self.bar(b,1,o=100,h=110,l=99,c=108);state=b.checkpoint();rules=self.rules(a,x)
        changes=[lambda s:s['exits']['orders'][0]['configuration'].update(trail_offset='4'),
                 lambda s:s['exits']['orders'][0]['targets'][0].update(trail_stop='108'),
                 lambda s:s['exits']['orders'][0]['targets'][0].update(activation='106'),
                 lambda s:s['exits']['orders'][0]['targets'][0].update(stop_eligible_bar=1),
                 lambda s:s['exits']['orders'][0]['targets'][0].update(activated_bar=True),
                 lambda s:s['exits']['orders'][0]['targets'].clear(),lambda s:s['exits']['orders'].clear()]
        for change in changes:
            candidate=deepcopy(state);change(candidate);new=LotBroker(b.policy,b.identity);before=deepcopy(new.__dict__)
            with self.assertRaises(ValueError):new.restore(candidate,count=b.count,last_time=b.last_time,sites=rules)
            self.assertEqual(new.phase,'ready');self.assertEqual(new.ledger.lots,{});self.assertEqual(new.exits.records,{})
        self.restore(b,a,x)

    def test_sixteen_distinct_lot_states_restore_with_pyramiding_zero(self):
        b=LotBroker(policy(),'capacity');a=entry(quantity=1);x=exit(trail_points=50,trail_offset=3)
        for i in range(16):self.bar(b,i,[a],o=100+i)
        self.bar(b,16,[x],o=116);self.assertEqual(len(b.exits.statistics()[0]['targets']),16);self.restore(b,a,x)

if __name__=='__main__':unittest.main()
