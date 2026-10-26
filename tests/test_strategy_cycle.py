"""Independent fill prices, phase counts, rollback and every-cut recovery."""
import copy,itertools,unittest
from dataclasses import replace
from unittest.mock import patch
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,HistoricalBar,HistoricalData
from packages.contracts.trading import BrokerSettings
from packages.contracts.strategy_cycle import PROFILE,FLAGS
from packages.contracts.live import RECEIVED_PROFILE
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.realtime import ReplayEngine
from test_observed_broker import Sink,CONTEXT,tape

def source(v,body,*,tick=False,fills=True,close=True):
    args=(',calc_on_every_tick='+str(tick).lower()+',calc_on_order_fills='+str(fills).lower() if v>=2 else '')
    if v>=4:args+=',process_orders_on_close='+str(close).lower()
    return f'//@version={v}\nstrategy("Cycle",default_qty_type=strategy.fixed,initial_capital=1000{args})\n'+body.replace('LONG','true' if v==1 else 'strategy.long')

PROTECTION='if bar_index==0 and strategy.position_size==0\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n    strategy.exit("X","L",limit=110)\nplot(strategy.position_size)'

class StrategyCycleTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.programs={}
    def engine(self,text,*,options=None,received=False,contexts=(CONTEXT,)):
        if text not in self.programs:self.programs[text]=self.session.compile(text)
        sink=Sink();engine=ReplayEngine(self.session,self.programs[text],contexts,'primary',sink,broker_settings=BrokerSettings(tick_size='1'),
            profile=RECEIVED_PROFILE if received else 'openpine.observed-realtime.1',calculation_profile=PROFILE,cycle_settings=options)
        return engine,sink
    def cuts(self,text,events,**kwargs):
        full,out=self.engine(text,**kwargs)
        for event in events:full.apply(event)
        for cut in range(len(events)+1):
            first,prefix=self.engine(text,**kwargs)
            for event in events[:cut]:first.apply(event)
            other,tail=self.engine(text,**kwargs);other.restore(loads(canonical(first.checkpoint())))
            self.assertEqual(first.status(),other.status())
            for event in events[cut:]:other.apply(event)
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(prefix.events+tail.events,out.events)
            self.assertEqual(other.status(),full.status());self.assertEqual(other.evaluation.checkpoint(),full.evaluation.checkpoint())
        return full,out
    def test_protection_after_close_fill_all_versions_both_clocks_every_cut(self):
        for v in range(1,7):
            opts=dict(zip(FLAGS,(False,True,True)))
            for received in (False,True):
                e,out=self.cuts(source(v,PROTECTION),tape(received=received),options=opts,received=received)
                self.assertEqual([x['value']['price'] for x in out.events if x['kind']=='fill'],['100','112'])
                self.assertEqual(e.simulation.broker.statistics()['equity'],'1012');self.assertEqual(e.calculation.cycle.count,4)
                self.assertEqual(e.calculation.cycle.observations,5);self.assertEqual([x[3] for x in out.rows],[1.0,0.0])
    def test_all_flag_combinations_have_explicit_phase_counts(self):
        for tick,fills,close in itertools.product((False,True),repeat=3):
            e,out=self.cuts(source(6,PROTECTION,tick=tick,fills=fills,close=close),tape())
            counts=e.calculation.cycle.counts
            self.assertEqual(counts['regular'],5 if tick else 2)
            self.assertEqual(sum(counts.values()),e.calculation.cycle.count)
            if not fills:self.assertEqual(counts['after_fill']+counts['after_close_fill'],0)
            if not close:self.assertEqual(counts['after_close_fill'],0)
            self.assertEqual(e.evaluation.count,2);self.assertEqual(len(out.rows),2)
    def test_ordinary_var_rolls_back_and_varip_counts_actual_passes(self):
        for v in (4,5,6):
            body='var a=0\nvarip b=0\na:=a+1\nb:=b+1\n'+PROTECTION+'\nplot(a)\nplot(b)'
            e,out=self.cuts(source(v,body),tape());self.assertEqual([r[3] for r in out.rows],[1.0,1,2,0.0,2,4])
    def test_array_aliases_replacement_and_history_commit_once(self):
        for v in (4,5,6):
            for replacement in ('','a:=array.new_float(0)\n'):
                body='varip a=array.new_float(0)\n'+replacement+'b=a\narray.push(b,close)\n'+PROTECTION+'\nplot(array.size(a))'
                e,out=self.cuts(source(v,body),tape());self.assertEqual([r[3] for r in out.rows],[1.0,1 if replacement else 2,0.0,1 if replacement else 4])
    def test_market_close_created_after_close_fill_waits_next_observation(self):
        body='if bar_index==0 and strategy.position_size==0\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n    strategy.close("L")\nplot(strategy.position_size)'
        e,out=self.cuts(source(6,body),tape());fills=[x['value'] for x in out.events if x['kind']=='fill']
        self.assertEqual([x['price'] for x in fills],['100','100']);self.assertEqual([x['observation']['sequence'] for x in fills],[0,1])
        self.assertEqual(e.calculation.cycle.count,4)
    def test_multiple_lot_fills_cause_one_batch_recalculation(self):
        body='if bar_index==0\n    strategy.entry("A",LONG)\n    strategy.entry("B",LONG)\nplot(strategy.position_size)'
        text=source(6,body,close=False).replace('initial_capital=1000','initial_capital=1000,pyramiding=2')
        e,out=self.cuts(text,tape());self.assertEqual(e.simulation.broker.fill_count,2)
        self.assertEqual(e.calculation.cycle.counts,{'regular':2,'after_fill':1,'after_close_fill':0})
    def test_recalculation_commands_survive_a_regular_pass_without_commands(self):
        body='varip n=0\nn:=n+1\nif bar_index==0\n    strategy.entry("L",LONG)\nif n==2\n    strategy.close("L")\nplot(n)'
        e,out=self.cuts(source(6,body,tick=True,close=False),tape())
        self.assertEqual([x['value']['price'] for x in out.events if x['kind']=='fill'],['100','94'])
    def test_close_processing_uses_close_not_old_cumulative_high_low(self):
        body='if bar_index==1\n    strategy.entry("L",LONG,limit=110)\nplot(strategy.position_size)'
        events=tape((100.,94.,112.,120.));e,out=self.cuts(source(6,body),events)
        self.assertEqual(e.simulation.broker.fill_count,0)
        self.assertEqual(len(e.simulation.broker.entries),1)
    def test_pending_limit_without_match_does_not_trigger_fill_pass(self):
        text=source(6,'if bar_index==0\n    strategy.entry("L",LONG,limit=50)\nplot(close)')
        e,_=self.cuts(text,tape());self.assertEqual(e.calculation.cycle.count,2)
    def test_source_availability_and_strict_flags(self):
        for v in range(1,7):
            for name,minimum in ((FLAGS[0],2),(FLAGS[1],2),(FLAGS[2],4)):
                for val in ('true','false'):
                    text=f'//@version={v}\nstrategy("T",{name}={val})\nplot(close)'
                    if v<minimum:
                        with self.assertRaises(OpenPineError):self.session.compile(text)
                    else:self.session.compile(text)
            for val in ('1','close>0'):
                with self.assertRaises(OpenPineError):self.session.compile(f'//@version={v}\nstrategy("T",calc_on_order_fills={val})\nplot(close)')
    def test_explicit_v1_reconstruction_and_conflicting_configuration(self):
        text=source(1,PROTECTION);e,_=self.cuts(text,tape(),options=dict(zip(FLAGS,(False,True,True))))
        self.assertEqual(e.calculation.cycle.count,4)
        for options in ({'foreign':True},{FLAGS[1]:1},{FLAGS[2]:False}):
            with self.assertRaises(ValueError):self.engine(source(6,PROTECTION),options=options)
    def test_duplicate_status_and_checkpoint_do_not_recalculate(self):
        e,_=self.engine(source(6,PROTECTION));e.apply(tape()[0]);saved=canonical(e.checkpoint())
        for _ in range(3):e.status();e.apply(tape()[0]);self.assertEqual(canonical(e.checkpoint()),saved)
        with self.assertRaises(ValueError):e.apply(replace(tape()[1],sequence=99))
        self.assertEqual(canonical(e.checkpoint()),saved)
    def test_corrupted_phase_counters_identity_memory_and_preview_are_rejected(self):
        e,_=self.engine(source(6,'varip n=0\nn:=n+1\n'+PROTECTION,tick=True))
        for event in tape()[:2]:e.apply(event)
        state=loads(canonical(e.checkpoint()));variants=[]
        for key,value in (('passes',999),('observations',999),('settings',{}),('last_pass',{'event_sequence':999,'phase':'regular','ordinal':1})):
            bad=copy.deepcopy(state);bad['intrabar']['cycle'][key]=value;variants.append(bad)
        bad=copy.deepcopy(state);bad['intrabar']['preview']['outputs'][0]['value']='bad';variants.append(bad)
        for bad in variants:
            other,_=self.engine(source(6,'varip n=0\nn:=n+1\n'+PROTECTION,tick=True))
            with self.assertRaises(ValueError):other.restore(bad)
    def test_pass_budget_is_enforced_before_execution(self):
        e,_=self.engine(source(6,PROTECTION))
        with patch('packages.execution.strategy_cycle.MAX_PASSES',1):
            with self.assertRaises(OpenPineError) as caught:e.apply(tape()[0])
        self.assertEqual(caught.exception.diagnostic.code,'CALCULATION_BUDGET')
    def test_historical_driver_uses_same_phases_with_disclosed_ohlc_model(self):
        for v in range(4,7):
            text=source(v,'varip n=0\nn:=n+1\n'+PROTECTION+'\nplot(n)')
            data=HistoricalData('primary','TEST:OBSERVED','1',(HistoricalBar(0,100.,100.,100.,100.,1.),HistoricalBar(60000,101.,100.,112.,94.,4.)),schema_version='openpine.historical-bars.2')
            _,result=self.session.run(text,data=data,broker_settings=BrokerSettings(tick_size='1'))
            self.assertEqual([x['value']['price'] for x in result.simulation['events'] if x['kind']=='fill'],['100','110'])
            self.assertEqual(result.simulation['statistics']['equity'],'1010');self.assertEqual(result.outputs[-1].values,(2,4))
    def test_all_kernel_overlays_can_commit_and_restore_without_extra_history(self):
        from test_technical_state import all_kernels,DATA
        for v in (4,5,6):
            prefix='' if v<5 else 'ta.'
            kernels=[f'{prefix}{x}' for x in ('sma(close,2)','ema(close,2)','rma(close,2)','rsi(close,2)','highest(close,2)','lowest(close,2)','variance(close,2)','stdev(close,2)','wma(close,2)','vwma(close,2)','change(close)','cum(close)','barssince(close>100)','highestbars(close,2)')]
            body=PROTECTION+'\n'+'\n'.join('plot('+x+')' for x in kernels)
            e,out=self.cuts(source(v,body),tape());self.assertEqual(e.evaluation.count,2)
            self.assertEqual(len(out.rows),2*(len(kernels)+1))

    def test_additional_passes_reuse_requested_value_without_advancing_context(self):
        from packages.contracts.contexts import MarketContext
        from packages.contracts.realtime import MarketEvent
        higher=MarketContext('higher','TEST:HIGHER','2','sampled.1')
        body='f() =>\n    varip n=0\n    n:=n+1\n    n\nplot(request.security("TEST:HIGHER","2",f()))\n'+PROTECTION
        def ev(seq,ctx,kind,t,at,rev):return MarketEvent(seq,ctx,kind,t,at,rev,100.,100.,100.,100.,float(rev+1))
        events=[ev(0,'higher','open',0,0,0),ev(1,'primary','close',0,60000,0),ev(2,'higher','update',0,70000,1),
            ev(3,'primary','open',60000,70001,0),ev(4,'primary','update',60000,70002,1),ev(5,'higher','update',0,70003,2),
            ev(6,'primary','close',60000,120000,2),ev(7,'higher','close',0,120001,3),ev(8,'primary','close',120000,180000,0)]
        e,out=self.cuts(source(6,body),events,received=True,contexts=(CONTEXT,higher))
        self.assertEqual([r[3] for r in out.rows[::2]],[1,3,4]);self.assertEqual(e.calculation.cycle.count,4)

    def test_old_broker_families_keep_manual_prices_during_fill_recalculation(self):
        from tools.observed_broker_fixtures import FAMILIES,EXPECTED,source as legacy,events
        for v in range(1,7):
            for family in FAMILIES:
                text=legacy(v,family)
                if v>=2:text=text.replace('commission_value=', 'calc_on_order_fills=true,commission_value=')
                e,out=self.cuts(text,events(family),options={FLAGS[1]:True})
                fills=[x['value'] for x in out.events if x['kind']=='fill'];oracle=EXPECTED[family]
                self.assertEqual([x['price'] for x in fills],oracle['prices'],(v,family))
                self.assertEqual([x['quantity'] for x in fills],oracle['quantities'])
                self.assertEqual([x['commission'] for x in fills],oracle['fees'])
                self.assertEqual(e.simulation.broker.statistics()['equity'],oracle['equity'])

    def test_close_processing_includes_slippage_and_both_commissions(self):
        text=source(6,PROTECTION).replace('initial_capital=1000','initial_capital=1000,commission_value=1,slippage=1')
        e,out=self.cuts(text,tape());fills=[x['value'] for x in out.events if x['kind']=='fill']
        self.assertEqual([x['price'] for x in fills],['101','112']);self.assertEqual([x['commission'] for x in fills],['1.01','1.12'])
        self.assertEqual(e.simulation.broker.statistics()['equity'],'1008.87')

    def test_bar_history_uses_final_pass_once(self):
        e,out=self.cuts(source(6,'varip n=0\nn:=n+1\n'+PROTECTION+'\nplot(n)\nplot(n[1])'),tape())
        self.assertEqual([r[3] for r in out.rows],[1.,2,None,0.,4,2])

    def test_all_generated_examples_have_handwritten_phase_and_output_expectations(self):
        from tools.strategy_cycle_fixtures import FAMILIES,source as original,events,expected,options,files
        from tools.specification import ROOT
        for name,raw in files().items():self.assertEqual((ROOT/'examples/strategy-cycle'/name).read_bytes(),raw)
        cases=0
        for v in range(1,7):
            for family in FAMILIES:
                if family=='arrays' and v<4:continue
                e,out=self.engine(original(v,family),options=options(family))
                for event in events():e.apply(event)
                oracle=expected(v,family);fills=[x['value'] for x in out.events if x['kind']=='fill']
                self.assertEqual([x['price'] for x in fills],oracle['prices']);self.assertEqual(e.simulation.broker.statistics()['equity'],oracle['equity'])
                self.assertEqual(e.calculation.cycle.count,oracle['passes']);self.assertEqual(e.evaluation.count,2)
                width=len(oracle['outputs']);self.assertEqual([[row[3] for row in out.rows[i::width]] for i in range(width)],oracle['outputs'])
                cases+=1
        self.assertEqual(cases,27)

    def test_composite_kernel_branches_commit_across_window_wrap(self):
        for v in (4,5,6):
            prefix='' if v<5 else 'ta.'
            body=PROTECTION+f'\n[m,s,h]={prefix}macd(close,2,3,2)\n[b,u,l]={prefix}bb(close,2,2)\nplot(m)\nplot(s)\nplot(h)\nplot(b)\nplot(u)\nplot(l)'
            events=tape()
            from packages.contracts.realtime import MarketEvent
            for i in range(2,10):events.append(MarketEvent(len(events),'primary','close',i*60000,(i+1)*60000,0,101.,101.,101.,101.,1.))
            e,out=self.cuts(source(v,body),events);self.assertEqual(e.evaluation.count,10)
            self.assertEqual([r[3] for r in out.rows[-3:]],[101.,101.,101.])

    def test_capability_inventory_reports_versioned_flags(self):
        from tools.capabilities import report
        value=report();self.assertEqual(value['strategy_cycle']['fill_header_versions'],[2,3,4,5,6])
        for v in value['versions']:
            rows=[a for a in v['apis'] if a['operation']=='simulation.strategy_cycle'];version=v['language_version']
            self.assertEqual(len(rows),int(version>=2))
            if rows:self.assertEqual(rows[0]['source_flag_availability'][FLAGS[2]],version>=4)

    def test_three_phase_reentry_stops_after_one_close_batch_and_restores(self):
        from packages.contracts.realtime import MarketEvent
        body='varip n=0\nn:=n+1\nif strategy.position_size==0\n    strategy.entry("L",LONG)\nelse\n    strategy.close("L")\nplot(n)'
        events=[MarketEvent(0,'primary','close',0,60000,0,100.,100.,100.,100.,1.),
            MarketEvent(1,'primary','close',60000,120000,0,101.,101.,101.,101.,1.)]
        e,out=self.cuts(source(6,body),events)
        self.assertEqual([r[3] for r in out.rows],[2,5])
        self.assertEqual([r['phase'] for r in e.calculation.cycle.recent],['after_fill','regular','after_close_fill'])
        self.assertEqual(e.simulation.broker.fill_count,3);self.assertEqual(e.simulation.broker.statistics()['position_size'],'1')
        self.assertEqual(len(e.simulation.broker.pending),1)

if __name__=='__main__':unittest.main()
