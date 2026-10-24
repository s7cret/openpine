"""Independent exact-quantity, fee, lifecycle and closed-state checks."""
import copy
from dataclasses import asdict
from decimal import Decimal
import unittest
from packages.contracts.trading import BrokerPolicy,BrokerSettings,OrderIntent
from packages.contracts.partial_exits import PROFILE,PartialCloseIntent,PartialProtectionIntent
from packages.simulation.broker.partial import PartialBroker

SITES={'entry','exit','close','cancel'}
def broker(fee='0',step='1',qty='10'):
    return PartialBroker(BrokerPolicy('1000',qty,fee,0,BrokerSettings('1',step,2),PROFILE),'a'*64)
def entry(qty='10',direction=1,name='L'):return OrderIntent('entry','entry',name,direction,qty)
def exit(name='A',qty='5',percent=None,stop='95',limit='110',target='L'):
    return PartialProtectionIntent('exit','exit',target,name,stop,limit,qty,percent)
def close(qty=None,percent=None,target='L'):return PartialCloseIntent('close','close',target,0,qty,percent)
def cancel(name='A'):return PartialProtectionIntent('cancel','cancel','',name)
def bar(b,index,commands=(),opening=100,high=101,low=99,closing=100):
    b.begin(index,index*60000,opening,closing,high,low)
    return b.commit(commands,index,index*60000)
def values(events,kind):return [e['value'] for e in events if e['kind']==kind]

class PartialBrokerTests(unittest.TestCase):
    def test_ten_units_first_take_five_then_stop_five(self):
        b=broker();bar(b,0,(entry(),exit(),exit('B',limit='120')))
        first=bar(b,1,high=111,closing=110)
        self.assertEqual(b.position['quantity'],'5');self.assertEqual(b.protections['B'].quantity,'5')
        self.assertEqual(values(first,'trade')[0]['gross_profit'],'50')
        second=bar(b,2,low=94,closing=95)
        self.assertIsNone(b.position);self.assertFalse(b.protections)
        self.assertEqual(b.statistics()['equity'],'1025')
        self.assertEqual((b.entry_count,b.fill_count,b.trade_count,b.closed_positions),(1,3,2,1))
        self.assertEqual(values(second,'trade')[0]['quantity'],'5')

    def test_independent_commissions_and_cumulative_allocation(self):
        b=broker(fee='1');bar(b,0,(entry(),exit(),exit('B',limit='120')))
        first=bar(b,1,high=111,closing=110);second=bar(b,2,low=94,closing=95)
        trades=values(first,'trade')+values(second,'trade')
        self.assertEqual([t['entry_commission'] for t in trades],['5','5'])
        self.assertEqual([t['exit_commission'] for t in trades],['5.5','4.75'])
        self.assertEqual(sum(Decimal(t['net_profit']) for t in trades),Decimal('4.75'))
        self.assertEqual((b.statistics()['equity'],b.statistics()['commission']),('1004.75','20.25'))

    def test_percentage_priority_rounding_and_no_overbooking(self):
        b=broker();bar(b,0,(entry(),exit(qty=None,percent='50'),exit('B',qty='8',percent='100')))
        self.assertEqual([o.quantity for o in b.protections.values()],['5','5'])
        c=broker(step='3',qty='12');bar(c,0,(entry('12'),exit(qty='4',percent='101'),exit('B',qty=None,percent='50')))
        self.assertEqual([o.quantity for o in c.protections.values()],['3','6'])
        d=broker();events=bar(d,0,(entry(),exit(qty='0',percent='50')))
        self.assertFalse(d.protections);self.assertEqual(values(events,'ignored')[0]['reason'],'no_unreserved_quantity')

    def test_manual_close_reduces_reservations_in_creation_order(self):
        b=broker();bar(b,0,(entry(),exit(),exit('B',limit='120')));bar(b,1,(close('2'),))
        events=bar(b,2)
        self.assertEqual(b.position['quantity'],'8')
        self.assertEqual([o.quantity for o in b.protections.values()],['5','3'])
        self.assertEqual(values(events,'reservation_reduced')[0]['quantity'],'3')

    def test_modification_cancellation_and_no_automatic_redistribution(self):
        b=broker();bar(b,0,(entry(),exit(),exit('B',limit='120')))
        bar(b,1,(cancel(),));self.assertEqual(b.protections['B'].quantity,'5')
        bar(b,2,(exit('C',qty='8',limit='130'),));self.assertEqual(b.protections['C'].quantity,'5')
        bar(b,3,(exit('B',qty='1',limit='125'),));self.assertEqual(b.protections['B'].quantity,'1')
        self.assertEqual(sum(Decimal(o.quantity) for o in b.protections.values()),6)

    def test_short_take_then_short_stop(self):
        b=broker();bar(b,0,(entry(direction=-1),exit(stop='105',limit='90'),exit('B',stop='105',limit='80')))
        bar(b,1,low=89,closing=90);bar(b,2,high=106,closing=105)
        self.assertEqual(b.statistics()['equity'],'1025');self.assertEqual(b.closed_positions,1)

    def test_open_crossings_precede_unknown_stop_touches(self):
        b=broker();bar(b,0,(entry(),exit('A',limit='110'),exit('B',stop='108',limit='120')))
        events=bar(b,1,opening=111,high=112,low=107,closing=110)
        triggers=values(events,'protection_trigger')
        self.assertEqual([(t['exit_id'],t['role'],t['basis']) for t in triggers],[('A','limit','open_crossing'),('B','stop','ohlc_touch')])
        self.assertEqual(b.statistics()['gross_realized'],'-15')

    def test_global_stop_before_limits_when_touch_order_unknown(self):
        b=broker();bar(b,0,(entry(),exit('A',stop='90',limit='110'),exit('B',stop='95',limit='120')))
        events=bar(b,1,high=111,low=94,closing=100)
        self.assertEqual([(x['exit_id'],x['role']) for x in values(events,'protection_trigger')],[('B','stop'),('A','limit')])

    def test_full_close_and_reversal_remove_exact_old_protection(self):
        b=broker();bar(b,0,(entry(),exit(),exit('B',limit='120')))
        bar(b,1,(entry('2',-1,'S'),close('3')))
        events=bar(b,2)
        self.assertFalse(b.protections);self.assertEqual((b.position['entry_id'],b.position['quantity']),('S','2'))
        self.assertEqual(values(events,'ignored')[-1]['reason'],'no_matching_position')

    def test_same_id_pending_reversal_can_receive_new_direction_protection(self):
        b=broker();bar(b,0,(entry(),))
        bar(b,1,(entry('2',-1),exit(qty=None,percent='100',stop='105',limit='90')))
        self.assertEqual(b.protections['A'].target_kind,'pending')
        events=bar(b,2,low=89,closing=90)
        self.assertIsNone(b.position);self.assertEqual(b.statistics()['equity'],'1020')
        self.assertEqual((b.entry_count,b.closed_positions,b.trade_count),(2,2,2))
        self.assertEqual(values(events,'protection_trigger')[0]['role'],'limit')

    def test_small_entry_fee_residue_is_exact_on_final_slice(self):
        b=broker(fee='0.006',qty='3');bar(b,0,(entry('3'),));bar(b,1,(close('1'),));a=bar(b,2,(close('1'),));c=bar(b,3,(close('1'),));d=bar(b,4)
        trades=values(a,'trade')+values(c,'trade')+values(d,'trade')
        self.assertEqual([t['entry_commission'] for t in trades],['0.01','0','0.01'])
        self.assertEqual(b.entry_fees,b.allocated_entry_fees)

    def test_failed_commit_cannot_be_retried_or_checkpointed(self):
        b=broker();bar(b,0,(entry(),));b.begin(1,60000,100,100,101,99)
        with self.assertRaises(ValueError):b.commit((exit(),exit('B',stop='120',limit='110')),1,60000)
        self.assertEqual(b.phase,'failed')
        with self.assertRaises(ValueError):b.checkpoint()
        with self.assertRaises(ValueError):b.commit((),1,60000)

    def test_decimal_audit_identity_keeps_sub_cent_principal_exact(self):
        from decimal import localcontext
        b=broker(step='0.0000000000000001',qty='0.1234567890123456')
        bar(b,0,(entry('0.1234567890123456'),),opening='1.1234567890123456',high='1.2',low='1.1',closing='1.1234567890123456')
        bar(b,1,(close('0.0234567890123456'),),opening='1.1234567890123456',high='1.2',low='1.1',closing='1.1234567890123456')
        events=bar(b,2,opening='1.2',high='1.3',low='1.1',closing='1.2')
        with localcontext() as context:
            context.prec=80
            gross=Decimal('0.0234567890123456')*(Decimal('1.2')-Decimal('1.1234567890123456'))
            self.assertEqual(Decimal(values(events,'trade')[0]['gross_profit']),gross)
            stats=b.statistics();principal=Decimal(b.position['quantity'])*Decimal(b.position['price'])
            self.assertEqual(Decimal(stats['cash_exact']),1000+gross-principal)
            self.assertEqual(Decimal(stats['equity_exact']),Decimal(stats['cash_exact'])+Decimal(b.position['quantity'])*Decimal('1.2'))

    def test_active_bracket_limit_is_bounded_across_bars(self):
        b=broker(qty='20');bar(b,0,(entry('20'),*[exit(str(i),qty='1') for i in range(15)]))
        bar(b,1,(exit('15',qty='1'),));self.assertEqual(len(b.protections),16)
        b.begin(2,120000,100,100,101,99)
        with self.assertRaisesRegex(ValueError,'16 active'):b.commit((exit('16',qty='1'),),2,120000)
        self.assertEqual(b.phase,'failed')

    def test_every_boundary_roundtrip_preserves_events_and_state(self):
        b=broker(fee='1');bar(b,0,(entry(),exit(),exit('B',limit='120')))
        for index,prices in enumerate(({'high':111,'closing':110},{'low':94,'closing':95},{}),1):
            state=copy.deepcopy(b.checkpoint());restored=broker(fee='1');restored.restore(state,count=b.count,last_time=b.last_time,sites=SITES)
            self.assertEqual(bar(b,index,**prices),bar(restored,index,**prices));self.assertEqual(b.checkpoint(),restored.checkpoint())

    def test_forged_reservation_and_fee_checkpoint_leave_destination_untouched(self):
        b=broker(fee='1');bar(b,0,(entry(),exit(),exit('B',limit='120')));bar(b,1,high=111,closing=110)
        original=b.checkpoint()
        mutations=[lambda s:s['protections'][0].update(quantity='6'),lambda s:s['position'].update(allocated_commission='0'),
            lambda s:s.update(entry_count=True),lambda s:s['position'].update(fill_id='b'*64),
            lambda s:s['protections'][0].update(target_id='b'*64)]
        for mutate in mutations:
            state=copy.deepcopy(original);mutate(state);target=broker(fee='1')
            with self.assertRaises(ValueError):target.restore(state,count=b.count,last_time=b.last_time,sites=SITES)
            self.assertEqual((target.count,target.position,target.phase),(0,None,'ready'))

    def test_invalid_quantities_and_levels_are_explicit(self):
        for intent in (close('-1'),close(percent='101'),exit(stop='95.5'),exit(stop='120',limit='110')):
            b=broker();bar(b,0,(entry(),));b.begin(1,60000,100,100,101,99)
            with self.assertRaises(ValueError):b.commit((intent,),1,60000)

if __name__=='__main__':unittest.main()
