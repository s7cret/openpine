"""Independent arithmetic, event and bounded-storage checks."""
import copy
import json
import math
import random
import unittest
from packages.runtime.history import StorageBudget
from packages.runtime.series import KernelError
from packages.runtime.series.signals import EventState,WindowState,IndexState,export_signal,restore_signal,fork_signal,SIGNAL_KINDS
from tools.signal_fixtures import oracle,VALUES,VOLUMES


def state(kind,base='float',v=6,budget=None):
    budget=budget or StorageBudget()
    return EventState(budget,kind,base,v) if kind in ('cum','barssince','valuewhen') else IndexState(budget,kind,v) if kind in ('rising','falling','highestbars','lowestbars') else WindowState(budget,kind)


def arguments(kind,x,i,length=3,volume=1.):
    return (x,) if kind=='cum' else (x is not None and x>3,) if kind=='barssince' else (i%2==0,x,1) if kind=='valuewhen' else (volume,x,length) if kind=='vwma' else (x,length)


class SignalTests(unittest.TestCase):
    def same(self,a,b):
        self.assertEqual(len(a),len(b))
        for x,y in zip(a,b):
            if y is None or type(y) is bool:self.assertIs(x,y)
            else:self.assertTrue(math.isclose(x,y,rel_tol=2e-14,abs_tol=2e-14),(x,y))

    def test_all_kernels_against_independent_list_and_fraction_oracles(self):
        rng=random.Random(742);xs=[None if i%11==0 else rng.uniform(-100,100) for i in range(250)];vs=[None if i%13==0 else float(i%7) for i in range(250)]
        for kind in SIGNAL_KINDS:
            for length in (1,2,3,7,32):
                s=state(kind);got=[s.update(*arguments(kind,x,i,length,vs[i])) for i,x in enumerate(xs)]
                self.same(got,oracle(kind,xs,vs,length=length))

    def test_weighted_sums_avoid_intermediate_overflow_and_cancellation(self):
        self.assertEqual(WindowState(StorageBudget(),'wma').update(1e308,1),1e308)
        s=WindowState(StorageBudget(),'wma');self.assertIsNone(s.update(1e308,2));self.assertEqual(s.update(1e308,2),1e308)
        s=WindowState(StorageBudget(),'sum')
        self.same([s.update(x,3) for x in [1e308,1.,-1e308]],[None,None,1.])
        s=WindowState(StorageBudget(),'vwma');self.assertIsNone(s.update(1e308,1e308,2));self.assertEqual(s.update(1e308,1e308,2),1e308)

    def test_overflow_is_explicit_and_cumulative_na_holds(self):
        s=state('cum');self.same([s.update(x) for x in (None,2.,None,-1.)],[None,2.,2.,1.])
        s=state('cum');s.update(1e308)
        with self.assertRaisesRegex(KernelError,'finite'):s.update(1e308)
        s=state('sum');s.update(1e308,2)
        with self.assertRaises(KernelError):s.update(1e308,2)

    def test_zero_missing_and_negative_volume(self):
        s=state('vwma');self.same([s.update(v,x,2) for v,x in ((0.,1.),(0.,2.),(None,4.),(1.,4.),(0.,8.))],[None,None,None,4.,4.])
        with self.assertRaisesRegex(KernelError,'nonnegative'):s.update(-1.,3.,2)

    def test_valuewhen_counts_missing_sources_as_events_and_preserves_integer_precision(self):
        s=state('valuewhen','int');n=2**62
        self.assertIsNone(s.update(True,n,1));self.assertEqual(s.update(True,None,1),n)
        self.assertIsNone(s.update(True,n+1,1));self.assertIsNone(s.update(False,n+2,1))
        self.assertEqual(s.update(True,n+2,1),n+1)

    def test_boolean_event_missing_policy_is_versioned(self):
        for v in range(1,7):
            s=state('valuewhen','bool',v);self.assertIs(s.update(False,True,0),False if v==6 else None)
            self.assertIs(s.update(True,False,0),False);self.assertIs(s.update(False,True,0),False)
            kind,payload=export_signal(s);restored=restore_signal(kind,json.loads(json.dumps(payload)),StorageBudget(),3,'bool',v)
            self.assertIs(restored.update(False,True,0),False)

    def test_barssince_ignores_na_conditions_and_resets_on_each_true(self):
        s=state('barssince');self.assertEqual([s.update(x) for x in (None,False,True,None,False,True)],[None,None,0,1,2,0])

    def test_momentum_uses_physical_reached_history_and_roc_zero_is_na(self):
        for name in ('mom','roc'):
            s=state(name);xs=[1.,None,0.,3.,-2.,0.,4.]
            self.same([s.update(x,2) for x in xs],oracle(name,xs,length=2))
        s=state('roc');s.update(1e308,1);self.assertEqual(s.update(1e308,1),0.)

    def test_trend_compares_the_entire_prior_window_not_adjacent_steps(self):
        for v in (1,6):
            s=state('rising',v=v)
            self.same([s.update(x,3) for x in [10.,1.,2.,3.,11.]],[False if v==6 else None]*3+[False,True])

    def test_extremum_latest_tie_offsets_and_na_holes(self):
        s=state('highestbars');xs=[None,4.,4.,None,2.,1.]
        self.assertEqual([s.update(x,3) for x in xs],[None,0,0,-1,-2,-1])

    def test_lengths_occurrences_and_parameter_changes_reject(self):
        for kind in ('wma','vwma','mom','rising','highestbars'):
            for n in (0,-1,100001,None,True,1.):
                with self.assertRaises(KernelError):state(kind).update(*( (1.,1.,n) if kind=='vwma' else (1.,n)))
            s=state(kind);s.update(*( (1.,1.,2) if kind=='vwma' else (1.,2)))
            with self.assertRaises(KernelError):s.update(*( (1.,1.,3) if kind=='vwma' else (1.,3)))
        for n in (-1,100000,None,True,1.):
            with self.assertRaises(KernelError):state('valuewhen').update(True,1.,n)

    def test_every_checkpoint_roundtrips_during_warmup_and_after_wrap(self):
        for kind in SIGNAL_KINDS:
            for cut in range(1,len(VALUES)+1):
                a=state(kind);prefix=[a.update(*arguments(kind,x,i,3,VOLUMES[i])) for i,x in enumerate(VALUES[:cut])]
                k,payload=export_signal(a);b=restore_signal(k,json.loads(json.dumps(payload)),StorageBudget(),cut,'float',6)
                tail=[b.update(*arguments(kind,x,i,3,VOLUMES[i])) for i,x in enumerate(VALUES[cut:],cut)]
                self.same(prefix+tail,oracle(kind,VALUES,VOLUMES,length=3))

    def test_preview_overlays_are_private_and_bounded(self):
        for kind in SIGNAL_KINDS:
            a=state(kind)
            for i in range(20):a.update(*arguments(kind,float(i),i,7))
            before=json.dumps(export_signal(a));b=fork_signal(a,copy.copy(a.budget))
            b.update(*arguments(kind,100.,20,7));self.assertEqual(json.dumps(export_signal(a)),before)
            if isinstance(a,IndexState):self.assertLessEqual(len(b.nodes.changed),5)
            elif a.slots:self.assertLessEqual(len(b.slots.changed),1)

    def test_memory_is_reserved_and_reaches_a_fixed_plateau(self):
        for kind in ('wma','vwma','valuewhen','highestbars'):
            b=StorageBudget();s=state(kind,budget=b)
            for i in range(100):s.update(*arguments(kind,float(i),i,7))
            cells=b.cells
            for i in range(100,1000):s.update(*arguments(kind,float(i),i,7))
            self.assertEqual(b.cells,cells);self.assertLessEqual(cells,28)
            with self.assertRaises(KernelError):state(kind,budget=StorageBudget(1)).update(*arguments(kind,1.,0,7))

    def test_malformed_checkpoints_are_closed_and_sequence_checked(self):
        for kind in SIGNAL_KINDS:
            s=state(kind)
            for i in range(5):s.update(*arguments(kind,float(i),i))
            _,payload=export_signal(s)
            for key,value in (('calls',False),('count',99),('extra',0)):
                bad=copy.deepcopy(payload);bad[key]=value
                with self.assertRaises(ValueError):restore_signal(kind,bad,StorageBudget(),5,'float',6)
            if 'slots' in payload:
                bad=copy.deepcopy(payload);bad['slots'].append(None)
                with self.assertRaises(ValueError):restore_signal(kind,bad,StorageBudget(),5,'float',6)


if __name__=='__main__':unittest.main()
