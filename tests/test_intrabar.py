"""Handwritten update counts, next-observation fills and every-cut restoration."""
import copy,unittest
from dataclasses import replace
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.contexts import MarketContext
from packages.contracts.intrabar import PROFILE
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.realtime import MarketEvent
from packages.contracts.trading import BrokerSettings
from packages.contracts.wire import canonical,loads
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from test_observed_broker import Sink,CONTEXT,tape

def strategy(v,body,*,header=True):
    return f'//@version={v}\nstrategy("Intrabar",default_qty_type=strategy.fixed,initial_capital=1000'+(',calc_on_every_tick=true' if header and v>=2 else '')+')\n'+body.replace('LONG','true' if v==1 else 'strategy.long')

def indicator(v,body):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Intrabar")\n'+body

class IntrabarTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.programs={}
    def engine(self,text,*,contexts=(CONTEXT,),received=False,explicit=True):
        if text not in self.programs:self.programs[text]=self.session.compile(text,'intrabar.pine')
        out=Sink();kwargs={'profile':RECEIVED_PROFILE} if received else {}
        return ReplayEngine(self.session,self.programs[text],tuple(contexts),'primary',out,broker_settings=BrokerSettings(tick_size='1') if text.splitlines()[1].startswith('strategy') else None,
            calculation_profile=PROFILE if explicit else None,**kwargs),out
    def cuts(self,text,events,**kwargs):
        full,out=self.engine(text,**kwargs)
        for event in events:full.apply(event)
        for cut in range(len(events)+1):
            first,prefix=self.engine(text,**kwargs)
            for event in events[:cut]:first.apply(event)
            state=loads(canonical(first.checkpoint()));resumed,tail=self.engine(text,**kwargs);resumed.restore(state)
            self.assertEqual(resumed.status(),first.status())
            for event in events[cut:]:resumed.apply(event)
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(prefix.events+tail.events,out.events)
            self.assertEqual(resumed.status(),full.status());self.assertEqual(resumed.evaluation.persistent,full.evaluation.persistent)
        return full,out
    def test_all_versions_new_commands_fill_on_next_received_sample(self):
        for v in range(1,7):
            text=strategy(v,'if close<95\n    strategy.entry("L",LONG)\nplot(strategy.position_size)')
            for received in (False,True):
                events=tape(received=received);e,out=self.cuts(text,events,received=received)
                fills=[x['value'] for x in out.events if x['kind']=='fill']
                self.assertEqual([f['price'] for f in fills],['112']);self.assertEqual(fills[0]['observation']['sequence'],3)
                self.assertEqual(e.evaluation.count,2);self.assertEqual(e.calculation.count,5)
    def test_var_and_varip_have_distinct_rollback_all_versions_with_varip(self):
        for v in (4,5,6):
            e,out=self.cuts(indicator(v,'var n=0\nvarip m=0\nn:=n+1\nm:=m+1\nplot(n)\nplot(m)'),tape(),explicit=False)
            self.assertEqual([r[3] for r in out.rows],[1,1,2,5])
    def test_array_aliases_and_replacement_restore_inside_candle(self):
        for v in (4,5,6):
            for replacement in ('','a:=array.new_float(0)\n'):
                text=indicator(v,'varip a=array.new_float(0)\n'+replacement+'b=a\narray.push(b,close)\nplot(array.size(a))')
                e,out=self.cuts(text,tape());self.assertEqual([r[3] for r in out.rows],[1,1 if replacement else 5])
                self.assertLessEqual(e.evaluation.heap.next_id,2*256+1)
            text=indicator(v,'var a=array.new_float(0)\nvarip b=a\narray.push(b,close)\nplot(array.size(a))')
            _,out=self.cuts(text,tape());self.assertEqual([r[3] for r in out.rows],[1,5])
    def test_local_function_varip_has_independent_call_sites(self):
        for v in (4,5,6):
            text=indicator(v,'f(x) =>\n    varip n=0\n    n:=n+x\n    n\na=f(1)\nb=f(2)\nplot(a)\nplot(b)')
            e,out=self.cuts(text,tape());self.assertEqual([r[3] for r in out.rows],[1,2,5,10])
    def test_switch_and_optional_function_restore_varip_at_every_event_cut(self):
        for v in (5,6):
            text=indicator(v,'f(x=2) =>\n    switch\n        close>0 =>\n            varip n=0\n            n:=n+x\n            n\n        => 0\na=f()\nb=f(3)\nplot(a)\nplot(b)')
            e,out=self.cuts(text,tape());self.assertEqual([r[3] for r in out.rows],[2,3,10,15])
    def test_conditional_varip_initializes_only_on_first_reached_update(self):
        for v in (4,5,6):
            text=indicator(v,'result=0\nif close<100\n    varip n=10\n    n:=n+1\n    result:=n\nplot(result)')
            events=tape((100.,94.,93.,92.));e,out=self.cuts(text,events)
            self.assertEqual([r[3] for r in out.rows],[0,13])
    def test_series_kernels_commit_once_and_barstate_counts_observed_passes(self):
        text=indicator(6,'varip n=0\nif barstate.isnew\n    n:=0\nn:=n+1\nplot(n)\nplot(ta.sma(close,2))\nplot(barstate.isconfirmed ? 1 : 0)')
        e,out=self.cuts(text,tape());self.assertEqual([r[3] for r in out.rows],[1,None,1,4,100.5,1])
    def test_requested_varip_advances_only_on_its_own_observations(self):
        text=indicator(6,'f() =>\n    varip n=0\n    n:=n+1\n    n\nplot(request.security("TEST:HIGHER","2",f()))')
        higher=MarketContext('higher','TEST:HIGHER','2','sampled.1')
        def ev(seq,ctx,kind,t,at,rev):return MarketEvent(seq,ctx,kind,t,at,rev,100.,100.,100.,100.,float(rev+1))
        events=[ev(0,'higher','open',0,0,0),ev(1,'primary','close',0,60000,0),ev(2,'higher','update',0,70000,1),
            ev(3,'primary','open',60000,70001,0),ev(4,'primary','update',60000,70002,1),ev(5,'higher','update',0,70003,2),
            ev(6,'primary','close',60000,120000,2),ev(7,'higher','close',0,120001,3),ev(8,'primary','close',120000,180000,0)]
        e,out=self.cuts(text,events,contexts=(CONTEXT,higher),received=True);self.assertEqual([r[3] for r in out.rows],[1,3,4])
    def test_cancel_and_modify_between_samples_never_fill_retroactively(self):
        for v in range(1,7):
            text=strategy(v,'if close==100\n    strategy.entry("L",LONG,limit=95)\nif close==99\n    strategy.cancel("L")\nif close==94\n    strategy.entry("L",LONG,limit=93)\nplot(strategy.position_size)')
            e,out=self.cuts(text,tape((99.,94.,92.,101.)))
            self.assertEqual([r['value']['price'] for r in out.events if r['kind']=='fill'],['92'])
    def test_protection_created_on_update_can_fill_at_next_update_same_bar(self):
        for v in range(1,7):
            text=strategy(v,'if bar_index==0\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n    strategy.exit("X","L",limit=110)\nplot(strategy.equity)')
            e,out=self.cuts(text,tape((100.,112.,105.,101.)))
            self.assertEqual([r['value']['price'] for r in out.events if r['kind']=='fill'],['100','112'])
            self.assertEqual(e.simulation.broker.statistics()['equity'],'1012')
    def test_duplicate_is_noop_and_failed_validation_keeps_cursor(self):
        e,out=self.engine(indicator(6,'varip n=0\nn:=n+1\nplot(n)'))
        events=tape();e.apply(events[0]);e.apply(events[1]);saved=canonical(e.checkpoint());before=copy.deepcopy(out.rows)
        e.apply(events[1]);self.assertEqual(canonical(e.checkpoint()),saved);self.assertEqual(out.rows,before)
        with self.assertRaises(ValueError):e.apply(replace(events[2],sequence=20))
        self.assertEqual(canonical(e.checkpoint()),saved)
    def test_version_gates_source_defaults_and_unsupported_recalculation_flags(self):
        for v in range(1,4):
            with self.assertRaises(OpenPineError):self.session.compile(indicator(v,'varip n=0\nplot(n)'))
        with self.assertRaises(OpenPineError):self.session.compile('//@version=1\nstrategy("T",calc_on_every_tick=true)\nplot(close)')
        for v in range(2,7):
            text=strategy(v,'plot(close)').replace('calc_on_every_tick=true','calc_on_every_tick=false')
            with self.assertRaises(ValueError):self.engine(text)
            engine,_=self.engine(text,explicit=False);self.assertIsNone(engine.calculation)
            self.session.compile(strategy(v,'plot(close)').replace('calc_on_every_tick','calc_on_order_fills'))
    def test_corrupted_intrabar_variables_preview_and_cursor_are_rejected(self):
        text=indicator(6,'varip a=array.new_float(0)\narray.push(a,close)\nplot(array.size(a))');e,_=self.engine(text)
        for event in tape()[:3]:e.apply(event)
        original=loads(canonical(e.checkpoint()));variants=[]
        for field,value in (('passes',100),('preview',None)):
            bad=copy.deepcopy(original);bad['intrabar'][field]=value;variants.append(bad)
        bad=copy.deepcopy(original);bad['intrabar']['variables']['values']['foreign']=1;variants.append(bad)
        bad=copy.deepcopy(original);bad['intrabar']['variables']['heap']['next_id']+=100;variants.append(bad)
        for bad in variants:
            other,_=self.engine(text)
            with self.assertRaises(ValueError):other.restore(bad)

    def test_all_original_examples_match_explicit_manual_expectations(self):
        from tools.intrabar_fixtures import FAMILIES,source,events,expected,files
        from tools.specification import ROOT
        for name,raw in files().items():self.assertEqual((ROOT/'examples/intrabar'/name).read_bytes(),raw)
        count=0
        for v in range(1,7):
            for family in FAMILIES:
                if family=='arrays' and v<4:continue
                e,out=self.engine(source(v,family))
                for event in events(family):e.apply(event)
                oracle=expected(v,family);self.assertEqual(e.calculation.count,oracle['passes'])
                if family in ('counter','arrays'):self.assertEqual([r[3] for r in out.rows],oracle['outputs'][0])
                else:
                    fills=[x['value'] for x in out.events if x['kind']=='fill']
                    self.assertEqual([x['price'] for x in fills],oracle['prices'])
                    self.assertEqual([x['commission'] for x in fills],oracle['fees'])
                    self.assertEqual(e.simulation.broker.statistics()['equity'],oracle['equity'])
                count+=1
        self.assertEqual(count,27)

    def test_inventory_reports_new_header_and_its_version_boundary(self):
        from tools.capabilities import report
        value=report();self.assertEqual(value['observed_calculation']['varip_versions'],[4,5,6])
        for row in value['versions']:
            headers=[a for a in row['apis'] if a['operation']=='simulation.strategy_intrabar']
            self.assertEqual(len(headers),int(row['language_version']>=2))
            if headers:self.assertIn('calc_on_every_tick',headers[0]['parameters'])

if __name__=='__main__':unittest.main()
