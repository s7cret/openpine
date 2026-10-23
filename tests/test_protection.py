"""Independent price/accounting expectations for the explicit OHLC model."""
import copy
import unittest
from packages.contracts.protection import PROFILE,ProtectionIntent
from packages.contracts.trading import BrokerPolicy,BrokerSettings,OrderIntent
from packages.simulation.broker.protective import ProtectiveBroker


def broker(*,slippage=0,commission='0'):
    return ProtectiveBroker(BrokerPolicy('1000','1',commission,slippage,BrokerSettings(tick_size='0.5'),PROFILE),'program')


def entry(name='L',direction=1):return OrderIntent('entry-site','entry',name,direction,'1')
def exit(stop='95',limit='110',name='X',target='L'):return ProtectionIntent('exit-site','exit',target,name,stop,limit)
def cancel(name='X'):return ProtectionIntent('cancel-site','cancel','',name)
def bar(b,index,opening=100,high=101,low=99,close=100,intents=()):
    b.begin(index,index*60000,opening,close,high=high,low=low)
    return b.commit(intents,index,index*60000)
def armed(**settings):
    b=broker(**settings);bar(b,0,intents=(entry(),exit()));return b
def values(events,kind):return [e['value'] for e in events if e['kind']==kind]


class ProtectiveBrokerTests(unittest.TestCase):
    def test_creation_bar_has_no_fill_then_both_touches_choose_stop(self):
        b=broker();bar(b,0,high=120,low=80,intents=(entry(),exit()))
        self.assertEqual(b.fill_count,0)
        events=bar(b,1,high=115,low=90,close=105)
        self.assertEqual([v['price'] for v in values(events,'fill')],['100','95'])
        self.assertEqual(b.statistics()['equity'],'995')
        self.assertTrue(values(events,'protection_trigger')[0]['ambiguous'])
        self.assertEqual(values(events,'cancelled')[-1]['reason'],'bracket_peer_filled')

    def test_long_and_short_limits_at_exact_touch(self):
        for direction,stop,limit,high,low in [(1,'95','110',110,99),(-1,'105','90',101,90)]:
            with self.subTest(direction=direction):
                b=broker();bar(b,0,intents=(entry(direction=direction),exit(stop,limit)))
                events=bar(b,1,high=high,low=low)
                self.assertEqual(b.statistics()['equity'],'1010')
                self.assertEqual(values(events,'protection_trigger')[0]['role'],'limit')
                self.assertFalse(values(events,'protection_trigger')[0]['ambiguous'])

    def test_short_both_touches_choose_stop(self):
        b=broker();bar(b,0,intents=(entry(direction=-1),exit('105','90')))
        events=bar(b,1,high=110,low=85)
        self.assertEqual(b.statistics()['equity'],'995')
        self.assertEqual(values(events,'fill')[-1]['price'],'105')
        self.assertTrue(values(events,'protection_trigger')[0]['ambiguous'])

    def test_stop_gap_slippage_and_commission_have_independent_arithmetic(self):
        b=armed(slippage=2,commission='1')
        entry_events=bar(b,1)
        events=bar(b,2,opening=90,high=115,low=85,close=100)
        self.assertEqual([v['price'] for v in values(entry_events+events,'fill')],['101','89'])
        self.assertEqual(b.statistics()['equity'],'986.1')
        self.assertEqual(b.statistics()['commission'],'1.9')
        self.assertEqual(values(events,'trade')[0]['net_profit'],'-13.9')
        self.assertFalse(values(events,'protection_trigger')[0]['ambiguous'])

    def test_limit_gap_improves_price_and_never_applies_slippage(self):
        b=armed(slippage=2,commission='1')
        entry_events=bar(b,1)
        events=bar(b,2,opening=120,high=125,low=90,close=100)
        self.assertEqual([v['price'] for v in values(entry_events+events,'fill')],['101','120'])
        self.assertEqual(b.statistics()['equity'],'1016.79')
        trigger=values(events,'protection_trigger')[0]
        self.assertEqual(trigger['basis'],'open_crossing');self.assertEqual(trigger['role'],'limit')
        self.assertFalse(trigger['ambiguous'])

    def test_short_stop_and_limit_gaps(self):
        for opening,high,low,stop,limit,equity,price in [(120,125,85,'105','90','980','120'),(80,110,75,'105','90','1020','80')]:
            b=broker();bar(b,0,intents=(entry(direction=-1),exit(stop,limit)))
            bar(b,1)
            events=bar(b,2,opening=opening,high=high,low=low)
            self.assertEqual(b.statistics()['equity'],equity)
            self.assertEqual(values(events,'fill')[-1]['price'],price)

    def test_modification_applies_from_next_bar_only(self):
        b=armed();first=b.protection.order_id
        events=bar(b,1,low=98,intents=(exit('99','111'),))
        self.assertEqual(b.fill_count,1);self.assertNotEqual(b.protection.order_id,first)
        self.assertEqual(values(events,'cancelled')[0]['reason'],'modified')
        events=bar(b,2,low=98)
        self.assertEqual(values(events,'fill')[-1]['price'],'99')

    def test_cancel_leaves_position_open_and_cancel_all_removes_pending(self):
        b=armed();bar(b,1,intents=(cancel(),))
        events=bar(b,2,high=120,low=80)
        self.assertEqual(b.fill_count,1);self.assertIsNotNone(b.position);self.assertFalse(values(events,'protection_trigger'))
        b=broker();bar(b,0,intents=(entry(),exit(),ProtectionIntent('all-site','cancel_all','','')))
        self.assertEqual(b.pending,[]);self.assertIsNone(b.protection)
        bar(b,1,high=120,low=80);self.assertEqual(b.fill_count,0)

    def test_cancelling_target_does_not_rebind_to_later_same_id(self):
        b=broker();events=bar(b,0,intents=(entry(),exit(),cancel('L'),entry()))
        self.assertIsNone(b.protection)
        self.assertIn('target_removed',[v['reason'] for v in values(events,'cancelled')])
        bar(b,1,high=120,low=80);self.assertEqual(b.fill_count,1)

    def test_close_and_reversal_cancel_exact_old_position_protection(self):
        for intent in [OrderIntent('close-site','close','L',0,None),entry('S',-1)]:
            b=armed();bar(b,1,intents=(intent,))
            events=bar(b,2,opening=90,high=120,low=80)
            self.assertIsNone(b.protection);self.assertFalse(values(events,'protection_trigger'))
            self.assertIn('position_closed_or_reversed',[v['reason'] for v in values(events,'cancelled')])
            self.assertEqual(b.position is None,intent.kind=='close')

    def test_exit_before_unknown_entry_is_ignored(self):
        b=broker();events=bar(b,0,intents=(exit(),entry()))
        self.assertEqual(values(events,'ignored')[0]['reason'],'no_matching_entry')
        bar(b,1,high=120,low=80);self.assertEqual(b.fill_count,1);self.assertIsNone(b.protection)

    def test_one_bracket_and_finite_aligned_ordered_levels_are_enforced(self):
        for command in [exit(None,None),exit('95.1','110'),exit('111','110'),exit('-1','110')]:
            with self.subTest(command=command):
                b=broker();b.begin(0,0,100,100,high=101,low=99)
                with self.assertRaises(ValueError):b.commit((entry(),command),0,0)
        b=armed();b.begin(1,60000,100,100,high=101,low=99)
        with self.assertRaises(ValueError):b.commit((exit(name='other'),),1,60000)

    def test_checkpoint_before_and_after_activation_matches_uninterrupted_events(self):
        for count in (1,2):
            b=armed()
            if count==2:bar(b,1)
            state=b.checkpoint();restored=broker()
            restored.restore(state,count=count,last_time=(count-1)*60000,sites={'entry-site','exit-site'})
            self.assertEqual(restored.checkpoint(),state)
            self.assertEqual(bar(restored,count,high=115,low=90),bar(b,count,high=115,low=90))
            self.assertEqual(restored.checkpoint(),b.checkpoint())

    def test_forged_binding_policy_site_levels_and_boundaries_are_rejected(self):
        b=armed();bar(b,1);state=b.checkpoint()
        mutations=[lambda s:s.update(schema_version='openpine.broker-state.1'),
            lambda s:s['policy'].update(profile_id='openpine.market-broker.1'),
            lambda s:s['protection'].update(target_id='unrelated'),
            lambda s:s['protection'].update(created_bar=2),
            lambda s:s['protection'].update(direction=True),
            lambda s:s['protection']['intent'].update(call_site_id='unknown'),
            lambda s:s['protection']['intent'].update(stop='111'),
            lambda s:s.pop('order_count'),lambda s:s.update(order_count='2'),
            lambda s:s.update(position=['malformed']),lambda s:s.update(pending=[{}])]
        for mutation in mutations:
            broken=copy.deepcopy(state);mutation(broken)
            with self.subTest(broken=broken),self.assertRaises(ValueError):
                broker().restore(broken,count=2,last_time=60000,sites={'entry-site','exit-site'})

    def test_inconsistent_ohlc_is_rejected_before_fills(self):
        b=armed()
        with self.assertRaises(ValueError):b.begin(1,60000,100,100,high=90,low=80)
        self.assertEqual(b.fill_count,0)


if __name__=='__main__':unittest.main()
