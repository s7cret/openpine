"""Independent event windows, frozen warmup and every-event joint restoration."""
import copy,unittest
from dataclasses import replace
from packages.contracts import HISTORICAL_PROFILE_ID
from packages.contracts.contexts import MarketContext
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical,loads
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from test_lower_tf import source
import test_lower_tf as historical_tests
from test_observed_broker import Sink

PRIMARY=MarketContext('primary','TEST:CHART','4','recorded.1')
LOWER=MarketContext('lower','TEST:LOWER','1','recorded.1')
CONTEXTS=(PRIMARY,LOWER)

def event(seq,cid,t,value,*,at=None,kind='close',revision=0):
    tf=4 if cid=='primary' else 1
    return MarketEvent(seq,cid,kind,t,t+tf*60000 if at is None else at,revision,value,value,value,value,10.)

def tape():
    rows=[]
    for i in range(16):
        rows.append(event(len(rows),'lower',i*60000,float(i+1)))
        if i%4==3:rows.append(event(len(rows),'primary',(i//4)*240000,100.))
    return rows

class LowerEventTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def engine(self,text=None,**kwargs):
        out=Sink();return ReplayEngine(self.session,self.session.compile(text or source()),CONTEXTS,'primary',out,**kwargs),out
    def test_both_versions_share_historical_private_typed_udf_and_history(self):
        body='base=close*2\nf(x) =>\n    var float total=0\n    total:=total+x\n    total\n[a,b,n,flags]=request.security_lower_tf("TEST:LOWER","1",[f(base),close[1],1,close>8])\nplot(array.sum(a))\nplot(array.sum(b))\nplot(array.sum(n))\nplot(array.get(flags,0) ? 1 : 0)'
        for v in (5,6):
            code=source(body,v);engine,out=self.engine(code)
            for e in tape():engine.apply(e)
            expected=historical_tests.LowerTFTests.outputs(self,code)
            self.assertEqual([r[-1] for r in out.rows],expected)
    def driver(self,*args,**kwargs):return historical_tests.LowerTFTests.driver(self,*args,**kwargs)
    def test_every_event_cut_restores_prefix_windows_and_private_state(self):
        code=source('a=request.security_lower_tf("TEST:LOWER","1",ta.cum(close))\nold=a[1]\nplot(array.sum(a))\nplot(na(old) ? 0 : array.sum(old))')
        full,out=self.engine(code)
        for e in tape():full.apply(e)
        for cut in range(len(tape())+1):
            first,prefix=self.engine(code)
            for e in tape()[:cut]:first.apply(e)
            second,tail=self.engine(code);second.restore(loads(canonical(first.checkpoint())))
            for e in tape()[cut:]:second.apply(e)
            self.assertEqual(prefix.rows+tail.rows,out.rows,cut)
            self.assertEqual(second.status(),full.status(),cut)
            self.assertEqual(second.checkpoint(),full.checkpoint(),cut)
    def test_frozen_calc_prefix_matches_history_then_continues_without_rebasing(self):
        code=source('a=request.security_lower_tf("TEST:LOWER","1",ta.cum(close),calc_bars_count=8)\nplot(array.size(a))\nplot(array.sum(a))')
        config={'schema_version':'openpine.lower-events.1','late':'reject','warmup':{'lower':{'count':16,'end_ms':960000}}}
        engine,out=self.engine(code,lower_tf=config)
        for e in tape():engine.apply(e)
        self.assertEqual([r[-1] for r in out.rows],[0,None,0,None,4,100.,4,308.])
        for i in range(16,20):engine.apply(event(engine.sequence+1,'lower',i*60000,float(i+1)))
        engine.apply(event(engine.sequence+1,'primary',960000,100.))
        self.assertEqual([r[-1] for r in out.rows[-2:]],[4,580.])
    def test_only_closed_children_visible_and_preview_does_not_commit(self):
        code=source('a=request.security_lower_tf("TEST:LOWER","1",close)\nplot(array.size(a))\nplot(array.sum(a))')
        e,out=self.engine(code)
        e.apply(event(0,'primary',0,100.,at=0,kind='open'))
        e.apply(event(1,'lower',0,1.,at=0,kind='open'))
        self.assertEqual([x['value'] for x in e.preview['outputs']],[0,None])
        e.apply(event(2,'lower',0,1.,at=30000,kind='update',revision=1))
        self.assertEqual(e.lower.pending_cells(),0)
        e.apply(event(3,'lower',0,1.,revision=2))
        self.assertEqual([x['value'] for x in e.preview['outputs']],[1,1.])
        self.assertEqual(out.rows,[]);self.assertEqual(e.evaluation.count,0)
        e.apply(event(4,'primary',0,100.,revision=1))
        self.assertEqual([r[-1] for r in out.rows],[1,1.]);self.assertEqual(e.lower.pending_cells(),0)
    def test_delayed_primary_future_windows_are_separate(self):
        e,out=self.engine(profile=RECEIVED_PROFILE)
        for i in range(8):e.apply(event(i,'lower',i*60000,float(i+1),at=900000+i))
        e.apply(event(8,'primary',0,100.,at=900010));self.assertEqual(out.rows[-1][-1],10.)
        self.assertEqual(e.lower.pending_cells(),4)
        other,_=self.engine(profile=RECEIVED_PROFILE);other.restore(loads(canonical(e.checkpoint())))
        other.apply(event(9,'primary',240000,100.,at=900011));self.assertEqual(other.lower.pending_cells(),0)
        self.assertEqual(list(other.lower.last_counts.values()),[4])
    def test_late_close_rejected_before_mutation_or_explicitly_marked(self):
        code=source('a=request.security_lower_tf("TEST:LOWER","1",ta.cum(close))\nplot(array.sum(a))')
        strict,out=self.engine(code,profile=RECEIVED_PROFILE)
        strict.apply(event(0,'primary',0,100.,at=900000));before=strict.checkpoint()
        with self.assertRaisesRegex(ValueError,'Late lower'):strict.apply(event(1,'lower',0,1.,at=900001))
        self.assertEqual(before,strict.checkpoint())
        config={'schema_version':'openpine.lower-events.1','late':'drop_with_metadata','warmup':{}}
        e,out=self.engine(code,profile=RECEIVED_PROFILE,lower_tf=config)
        e.apply(event(0,'primary',0,100.,at=900000));e.apply(event(1,'lower',0,1.,at=900001))
        self.assertEqual(out.rows[-1][-1],None);self.assertEqual(e.lower.pending_cells(),0)
        e.apply(event(2,'lower',240000,5.,at=900002));e.apply(event(3,'primary',240000,100.,at=900003))
        self.assertEqual(out.rows[-1][-1],6.);self.assertEqual(e.lower.summary()['late_closes'],1)
        other,_=self.engine(code,profile=RECEIVED_PROFILE,lower_tf=config);other.restore(loads(canonical(e.checkpoint())))
        self.assertEqual(other.status(),e.status())
    def test_observed_unclosed_child_strict_or_incomplete_metadata(self):
        for policy in ('reject','drop_with_metadata'):
            config={'schema_version':'openpine.lower-events.1','late':policy,'warmup':{}}
            e,out=self.engine(profile=RECEIVED_PROFILE,lower_tf=config)
            e.apply(event(0,'lower',0,1.,at=0,kind='open'))
            if policy=='reject':
                with self.assertRaisesRegex(ValueError,'Primary close'):e.apply(event(1,'primary',0,100.,at=900000))
            else:
                e.apply(event(1,'primary',0,100.,at=900000));self.assertEqual(e.lower.summary()['incomplete_primary_closes'],1)
                e.apply(event(2,'lower',0,1.,at=900001,revision=1));self.assertEqual(e.lower.summary()['late_closes'],1)
    def test_bad_summary_and_window_restore_poison_runtime(self):
        engine,_=self.engine();engine.apply(tape()[0]);state=loads(canonical(engine.checkpoint()))
        for change in ('count','end','sequence','summary'):
            bad=copy.deepcopy(state);row=next(iter(bad['requests'].values()))['history'][0]
            if change=='summary':bad['lower_tf']['last_counts']={'forged':1}
            elif change=='count':row['count']=True
            elif change=='end':row['end']+=1
            else:row['sequence']=999
            other,_=self.engine()
            with self.assertRaises(ValueError):other.restore(bad)
            self.assertTrue(other.evaluation.closed)

    def test_phase_scheduler_varip_aliases_and_fill_restore_every_event(self):
        from packages.contracts.trading import BrokerSettings
        from packages.contracts.strategy_cycle import PROFILE
        from test_strategy_cycle import source as strategy_source
        code=strategy_source(6,'a=request.security_lower_tf("TEST:LOWER","1",close)\nb=a\nif array.size(b)>0\n    array.set(b,0,99)\nvarip n=0\nn:=n+1\nif bar_index==0 and strategy.position_size==0\n    strategy.entry("L",LONG)\nif bar_index==1\n    strategy.close("L")\nplot(array.size(a))\nplot(array.sum(a))\nplot(n)',tick=True)
        events=[event(0,'primary',0,100.,at=0,kind='open')]
        for old in tape():
            events.append(replace(old,sequence=len(events),revision=1 if old.context_id=='primary' else 0))
            if old.context_id=='primary' and old.time<720000:events.append(event(len(events),'primary',old.time+240000,100.,at=old.time+240000,kind='open'))
        kwargs={'broker_settings':BrokerSettings(tick_size='1'),'calculation_profile':PROFILE}
        full,out=self.engine(code,**kwargs)
        for e in events:full.apply(e)
        self.assertEqual(full.simulation.broker.fill_count,2)
        self.assertEqual([r[-1] for r in out.rows][::3],[4]*4)
        for cut in range(len(events)+1):
            first,prefix=self.engine(code,**kwargs)
            for e in events[:cut]:first.apply(e)
            other,tail=self.engine(code,**kwargs);other.restore(loads(canonical(first.checkpoint())))
            for e in events[cut:]:other.apply(e)
            self.assertEqual(prefix.rows+tail.rows,out.rows,cut);self.assertEqual(prefix.events+tail.events,out.events,cut)
            self.assertEqual(other.status(),full.status(),cut)

    def test_pending_budget_and_warmup_boundary_fail_before_consuming_event(self):
        from unittest.mock import patch
        e,_=self.engine();e.apply(tape()[0]);before=e.checkpoint()
        with patch('packages.execution.lower_events.MAX_CELLS',1):
            with self.assertRaisesRegex(ValueError,'budget'):e.apply(tape()[1])
        self.assertEqual(before,e.checkpoint())
        config={'schema_version':'openpine.lower-events.1','late':'reject','warmup':{'lower':{'count':2,'end_ms':60000}}}
        e,_=self.engine(lower_tf=config);e.apply(tape()[0]);before=e.checkpoint()
        with self.assertRaisesRegex(ValueError,'warmup'):e.apply(tape()[1])
        self.assertEqual(before,e.checkpoint())

    def test_ties_gaps_retransmissions_and_explicit_invalid_timeframe(self):
        e,out=self.engine();e.apply(tape()[0]);before=e.checkpoint();e.apply(tape()[0]);self.assertEqual(before,e.checkpoint())
        with self.assertRaisesRegex(ValueError,'duplicate'):e.apply(replace(tape()[0],volume=20.))
        # No fabricated child for an entirely absent interval.
        e.apply(event(1,'primary',0,100.));e.apply(event(2,'primary',240000,100.))
        self.assertEqual([r[-1] for r in out.rows],[1.,None])
        with self.assertRaises(ValueError):e.apply(event(3,'lower',480000,9.,at=480000))
        code=source('a=request.security_lower_tf("TEST:CHART","4",close,ignore_invalid_timeframe=true)\nplot(array.size(a))')
        e,out=self.engine(code)
        for old in tape():e.apply(old)
        self.assertEqual([r[-1] for r in out.rows],[0]*4)
        restored,_=self.engine(code);restored.restore(loads(canonical(e.checkpoint())))
        self.assertEqual(restored.status(),e.status())

if __name__=='__main__':unittest.main()
