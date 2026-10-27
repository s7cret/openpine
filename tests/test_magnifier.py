"""Independent lower-ordering oracles, availability and every-point recovery."""
import copy,unittest
from dataclasses import replace
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalBar,HistoricalData,OpenPineError
from packages.contracts.magnifier import PROFILE,minute_pair
from packages.contracts.wire import canonical,loads
from packages.contracts.strategy_ir import broker_policy
from packages.contracts.trading import BrokerSettings
from packages.execution.driver import HistoricalDriver,MemorySource
from packages.execution.session import Session
from packages.execution.magnifier_feed import coverage
from test_observed_broker import Sink

def candle(t,o,h,l,c,v=1.):return HistoricalBar(t,float(c),float(o),float(h),float(l),float(v))
def datasets(reverse=False):
    primary=HistoricalData('primary','TEST:MAGNIFIER','2',(candle(0,100,100,100,100,2),candle(120000,100,120,80,100,2)),schema_version='openpine.historical-bars.2')
    rows=(candle(120000,100,100,80,90),candle(180000,90,120,90,100)) if reverse else (candle(120000,100,120,100,110),candle(180000,110,110,80,100))
    lower=HistoricalData('lower','TEST:MAGNIFIER','1',(candle(0,100,100,100,100),candle(60000,100,100,100,100),*rows),schema_version='openpine.historical-bars.2')
    return primary,lower
def config(**extra):return {'schema_version':PROFILE,'snapshot_id':'a'*64,'missing':'reject','cycle_settings':{'calc_on_order_fills':True},'checkpoint_every_children':0,**extra}
BODY='if bar_index==0 and strategy.position_size==0\n    strategy.entry("L",LONG)\nif strategy.position_size>0\n    strategy.exit("X","L",stop=90,limit=110)\nplot(strategy.equity)'
def source(v=6,body=BODY,extra=''):
    return f'//@version={v}\nstrategy("Magnifier",default_qty_type=strategy.fixed,initial_capital=1000'+(',use_bar_magnifier=true' if v>=5 else '')+extra+')\n'+body.replace('LONG','true' if v==1 else 'strategy.long')

class MagnifierTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID);cls.programs={}
    def driver(self,text=None,*,data=None,settings=None,sink=None):
        text=text or source()
        if text not in self.programs:self.programs[text]=self.session.compile(text)
        program=self.programs[text];runtime=self.session.validate_program(program);out=sink or Sink();a,b=data or datasets()
        return HistoricalDriver(runtime.prepare(program,output_sink=out),MemorySource(a),simulation_policy=broker_policy(program,BrokerSettings(tick_size='1')),
            magnifier=settings or config(),lower_source=MemorySource(b)),out
    def test_same_parent_different_lower_order_has_independent_prices_all_versions(self):
        for v in range(1,7):
            for reverse,expected in ((False,['100','120']),(True,['100','80'])):
                d,out=self.driver(source(v),data=datasets(reverse));d.run()
                self.assertEqual([e['value']['price'] for e in out.events if e['kind']=='fill'],expected)
                self.assertEqual(d.simulation.broker.statistics()['equity'],'980' if reverse else '1020')
                self.assertEqual(d.count,2);self.assertEqual(d.cycle.counts,{'regular':2,'after_fill':2,'after_close_fill':0})
                self.assertEqual(d.magnifier.summary()['model_points'],16)
                fill=next(e for e in out.events if e['kind']=='fill');self.assertEqual(fill['value']['observation']['child_time'],120000)
    def test_every_point_cut_including_first_parent_preserves_outputs_journal_and_state(self):
        text=source(6,'varip n=0\nn:=n+1\n'+BODY+'\nplot(n)')
        full,out=self.driver(text);full.run();expected=full.checkpoint()
        for cut in range(1,17):
            first,prefix=self.driver(text);states=[]
            first.run(checkpoint=lambda d:states.append(loads(canonical(d.checkpoint()))),pause=lambda:first.magnifier.observations>=cut)
            second,tail=self.driver(text);second.restore(states[-1]);second.run()
            self.assertEqual(prefix.rows+tail.rows,out.rows,cut);self.assertEqual(prefix.events+tail.events,out.events,cut)
            self.assertEqual(second.checkpoint(),expected,cut)
        lines=[]
        for branch,direction in (('if strategy.position_size>0','strategy.short'),('else','strategy.long')):
            lines.append(branch);lines.extend(f'    strategy.entry("L{i}",{direction})' for i in range(16))
        text=source(body='\n'.join(lines)+'\nplot(strategy.equity)',extra=',pyramiding=16,process_orders_on_close=true')
        a=HistoricalData('primary','TEST:MAGNIFIER','2',tuple(candle(i*120000,100,100,100,100,2) for i in range(5)),schema_version='openpine.historical-bars.2')
        b=HistoricalData('lower','TEST:MAGNIFIER','1',tuple(candle(i*60000,100,100,100,100) for i in range(10)),schema_version='openpine.historical-bars.2')
        full,_=self.driver(text,data=(a,b));full.run();self.assertEqual(full.simulation.broker.order_count,672)
        other,_=self.driver(text,data=(a,b));other.restore(loads(canonical(full.checkpoint())));self.assertEqual(other.checkpoint(),full.checkpoint())
        text=source(body='strategy.risk.max_drawdown(30,strategy.cash)\n'+'\n'.join(lines)+'\nplot(strategy.equity)',extra=',pyramiding=16,process_orders_on_close=true')
        a=HistoricalData('primary','TEST:MAGNIFIER','120',tuple(candle(i*7200000,100,100,100,100,120) for i in range(2)),schema_version='openpine.historical-bars.2')
        b=HistoricalData('lower','TEST:MAGNIFIER','1',tuple(candle(i*60000,100,100,100,100) for i in range(240)),schema_version='openpine.historical-bars.2')
        full,_=self.driver(text,data=(a,b));full.run()
        self.assertEqual((full.simulation.broker.fill_count,full.simulation.broker.order_count),(8193,7744))
        other,_=self.driver(text,data=(a,b));other.restore(loads(canonical(full.checkpoint())));self.assertEqual(other.checkpoint(),full.checkpoint())
    def test_synthetic_prices_never_trigger_calc_on_every_tick(self):
        d,out=self.driver(source(extra=',calc_on_every_tick=true'));d.run()
        self.assertEqual(d.cycle.counts['regular'],2);self.assertEqual(len(out.rows),2)
    def test_after_fill_cannot_see_future_parent_high_or_low(self):
        body='varip seen=0\nif bar_index==0\n    strategy.entry("L",LONG)\nif strategy.position_size>0 and high==100 and low==100\n    seen:=1\nplot(seen)'
        d,out=self.driver(source(body=body));d.run();self.assertEqual([r[3] for r in out.rows],[0,1])
    def test_gap_reject_or_explicit_whole_parent_fallback(self):
        a,b=datasets();b=replace(b,bars=b.bars[:-1])
        with self.assertRaisesRegex(ValueError,'Incomplete'):coverage(MemorySource(a),MemorySource(b),'reject')
        d,out=self.driver(data=(a,b),settings=config(missing='parent_ohlc'));d.run()
        self.assertEqual(d.magnifier.summary()['fallback_bars'],1);self.assertEqual(d.magnifier.summary()['model_points'],12)
        self.assertTrue(any(e['value'].get('observation',{}).get('fallback') for e in out.events))
    def test_complete_conflict_rejected_even_when_fallback_requested(self):
        a,b=datasets();b=replace(b,bars=(*b.bars[:-1],replace(b.bars[-1],volume=3.)))
        with self.assertRaisesRegex(ValueError,'conflict'):coverage(MemorySource(a),MemorySource(b),'parent_ohlc')
    def test_duplicate_disorder_wrong_symbol_and_timeframe_rejected(self):
        a,b=datasets()
        for other in (replace(b,symbol='OTHER'),replace(b,timeframe='2'),replace(b,bars=(b.bars[0],*b.bars)),replace(b,bars=(b.bars[1],b.bars[0],*b.bars[2:]))):
            with self.assertRaises(ValueError):coverage(MemorySource(a),MemorySource(other),'parent_ohlc')
        for pair in (('15','2'),('1','1'),('D','1'),('2','01')):
            with self.assertRaises(ValueError):minute_pair(*pair)
    def test_header_gates_source_conflicts_and_missing_lower_input(self):
        for v in range(1,5):
            with self.assertRaises(OpenPineError):self.session.compile(source(v,extra=',use_bar_magnifier=true'))
        for v in (5,6):
            program=self.session.compile(source(v));a,b=datasets()
            with self.assertRaisesRegex(ValueError,'pinned'):self.session.execute(program,data=a)
            with self.assertRaisesRegex(ValueError,'conflicts'):self.driver(source(v).replace('use_bar_magnifier=true','use_bar_magnifier=false'))
    def test_corrupt_inside_parent_cursor_cannot_restore(self):
        d,_=self.driver();states=[];d.run(checkpoint=lambda d:states.append(loads(canonical(d.checkpoint()))),pause=lambda:d.magnifier.observations==9)
        for field,value in (('within',2),('child_cursor',0),('covered',2),('observations',10)):
            state=copy.deepcopy(states[0]);state['cursor'][field]=value
            other,_=self.driver()
            with self.assertRaises(ValueError,msg=field):other.restore(state)
        state=copy.deepcopy(states[0]);state['runtime']['context_id']='another-source'
        other,_=self.driver()
        with self.assertRaisesRegex(ValueError,'Runtime differs'):other.restore(state)

    def test_existing_six_order_families_keep_manual_arithmetic_all_versions(self):
        from tools.observed_broker_fixtures import FAMILIES,EXPECTED,source as original,events as tape
        for v in range(1,7):
            for family in FAMILIES:
                prices=[e.close for e in tape(family)[1:]];n=len(prices)
                a=HistoricalData('primary','TEST:MAGNIFIER',str(n),(candle(0,100,100,100,100,n),candle(n*60000,prices[0],max(prices),min(prices),prices[-1],n)),schema_version='openpine.historical-bars.2')
                b=HistoricalData('lower','TEST:MAGNIFIER','1',tuple(candle(i*60000,p,p,p,p) for i,p in enumerate([100.]*n+prices)),schema_version='openpine.historical-bars.2')
                d,out=self.driver(original(v,family),data=(a,b),settings=config(cycle_settings={'calc_on_order_fills':False}));d.run()
                fills=[e['value'] for e in out.events if e['kind']=='fill'];expected=EXPECTED[family]
                self.assertEqual([f['price'] for f in fills],expected['prices'],(v,family))
                self.assertEqual([f['commission'] for f in fills],expected['fees'],(v,family))
                self.assertEqual(d.simulation.broker.statistics()['equity'],expected['equity'],(v,family))

    def test_requested_close_available_only_when_its_own_interval_has_closed(self):
        from packages.contracts.contexts import MarketContext,ContextBinding
        from packages.execution.contexts import ContextScheduler
        text=source(body='varip early=0.0\nr=request.security("TEST:MAGNIFIER","4",close)\nif bar_index==0\n    strategy.entry("L",LONG)\nif strategy.position_size>0 and high==100\n    early:=nz(r,0)\nplot(early)\nplot(r)')
        program=self.session.compile(text);runtime=self.session.validate_program(program);a,b=datasets()
        higher=HistoricalData('higher','TEST:MAGNIFIER','4',(candle(0,500,800,500,777,4),),schema_version='openpine.historical-bars.2')
        def make():
            primary=MemorySource(a);selected=MemorySource(higher);selected.bind_identity();out=Sink()
            contexts=ContextScheduler(program,runtime,MarketContext('primary',a.symbol,a.timeframe,'memory.1'),primary,
                [(ContextBinding(MarketContext('higher',a.symbol,'4','memory.1'),selected.identity.sha256),selected)])
            return HistoricalDriver(runtime.prepare(program,output_sink=out),primary,contexts,broker_policy(program,BrokerSettings(tick_size='1')),
                magnifier=config(),lower_source=MemorySource(b)),out
        full,out=make();full.run();self.assertEqual([r[3] for r in out.rows],[0,None,0.,777.])
        for cut in (1,8,9,12,15):
            first,prefix=make();states=[];first.run(checkpoint=lambda d:states.append(loads(canonical(d.checkpoint()))),pause=lambda:first.magnifier.observations==cut)
            other,tail=make();other.restore(states[-1]);other.run();self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(other.checkpoint(),full.checkpoint())
        program=self.session.compile(text.replace('"4",close)','"4",close,lookahead=barmerge.lookahead_on)'));runtime=self.session.validate_program(program)
        with self.assertRaisesRegex(ValueError,'lookahead_on'):make()

if __name__=='__main__':unittest.main()
