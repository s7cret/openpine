"""Independent child-window values, typed ownership, availability and restoration."""
import copy,unittest
from dataclasses import replace
from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalBar,HistoricalData,OpenPineError,ValueType
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.contracts.context_ir import request_nodes
from packages.contracts.codec import encode,decode
from packages.contracts import ProgramIR
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import MemorySource
from packages.marketdata import data_identity

def data():
    parent=HistoricalData('primary','TEST:CHART','4',tuple(HistoricalBar(i*240000,100.,100.,100.,100.,10.) for i in range(4)),schema_version='openpine.historical-bars.2')
    lower=HistoricalData('lower','TEST:LOWER','1',tuple(HistoricalBar(i*60000,float(i+1),float(i+1),float(i+1),float(i+1),10.) for i in range(16)),schema_version='openpine.historical-bars.2')
    return parent,lower

def source(body='a=request.security_lower_tf("TEST:LOWER","1",close)\nplot(array.sum(a))',v=6):
    return f'//@version={v}\n'+('indicator' if v>=5 else 'study')+'("Lower requests")\n'+body+'\n'

class Sink:
    def __init__(self):self.rows=[]
    def emit(self,site,title,typ,value,index,time):self.rows.append((index,time,site,value))

class LowerTFTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def driver(self,text=None,*,datasets=None,sink=None):
        p=self.session.compile(text or source());a,b=datasets or data();out=sink or Sink()
        primary=MarketContext(a.context_id,a.symbol,a.timeframe,'uploaded-file.1')
        context=MarketContext(b.context_id,b.symbol,b.timeframe,'uploaded-file.1')
        binding=ContextBinding(context,data_identity(b).sha256)
        return self.session.stream_driver(p,MemorySource(a),out,primary_context=primary,
            context_sources=((binding,MemorySource(b)),)),out
    def outputs(self,text=None,**kwargs):
        d,s=self.driver(text,**kwargs);d.run();return [row[-1] for row in s.rows]
    def test_versioned_scalar_arrays_are_chronological_and_other_symbol(self):
        for v in (5,6):self.assertEqual(self.outputs(source(v=v)),[10.,26.,42.,58.])
        for v in range(1,5):
            with self.assertRaises(OpenPineError) as caught:self.session.compile(source('request.security_lower_tf("TEST:LOWER","1",close)',v=v))
            self.assertEqual(caught.exception.diagnostic.code,'LOWER_TF_VERSION')
    def test_mixed_tuple_arrays_keep_int_float_and_bool_types_and_saved_ir(self):
        text=source('[n,p,b]=request.security_lower_tf("TEST:LOWER","1",[1,close,close>8])\nplot(array.sum(n))\nplot(array.avg(p))\nplot(array.get(b,0) ? 1 : 0)')
        d,s=self.driver(text);p=decode(encode(d.program),ProgramIR);self.session.validate_program(p)
        self.assertEqual([t.base for t in request_nodes(p)[0].value_type.elements],['array.int','array.float','array.bool'])
        d.run();self.assertEqual([r[-1] for r in s.rows],[4,2.5,0,4,6.5,0,4,10.5,1,4,14.5,1])
    def test_gaps_empty_arrays_and_no_primary_ohlcv_requirement(self):
        a,b=data();b=replace(b,bars=tuple(x for i,x in enumerate(b.bars) if not 4<=i<8))
        self.assertEqual(self.outputs(source('a=request.security_lower_tf("TEST:LOWER","1",close)\nplot(array.size(a))\nplot(array.sum(a))'),datasets=(a,b)),[4,10.,0,None,4,42.,4,58.])
    def test_history_window_is_last_actual_rows_and_private_state_starts_there(self):
        text=source('a=request.security_lower_tf("TEST:LOWER","1",ta.cum(close),calc_bars_count=8)\nplot(array.size(a))\nplot(array.sum(a))')
        # Independent running sums of 9..16: 9,19,30,42,55,69,84,100.
        self.assertEqual(self.outputs(text),[0,None,0,None,4,100.,4,308.])
    def test_dependency_graph_udf_history_and_separate_call_states(self):
        text=source('base=close*2\nf(x) =>\n    var float total=0\n    total:=total+x\n    total\n[a,b]=request.security_lower_tf("TEST:LOWER","1",[f(base),close[1]])\nplot(array.sum(a))\nplot(array.sum(b))')
        self.assertEqual(self.outputs(text),[40.,6.,200.,22.,488.,38.,904.,54.])
    def test_mutation_alias_copy_and_history_do_not_modify_future_windows(self):
        text=source('a=request.security_lower_tf("TEST:LOWER","1",close)\nb=a\nc=array.copy(a)\narray.set(b,0,99)\nold=a[1]\nplot(array.sum(c))\nplot(array.sum(a))\nplot(na(old) ? 0 : array.get(old,0))')
        self.assertEqual(self.outputs(text),[10.,108.,0,26.,120.,99.,42.,132.,99.,58.,144.,99.])
    def test_every_primary_checkpoint_preserves_windows_and_private_state(self):
        text=source('a=request.security_lower_tf("TEST:LOWER","1",ta.cum(close),calc_bars_count=12)\nprevious=a[1]\nplot(array.sum(a))\nplot(na(previous) ? 0 : array.sum(previous))')
        full,out=self.driver(text);full.run();expected=full.checkpoint()
        for cut in range(1,5):
            first,prefix=self.driver(text);saved=[]
            first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
            second,tail=self.driver(text);second.restore(saved[-1]);second.run()
            self.assertEqual(prefix.rows+tail.rows,out.rows);self.assertEqual(second.checkpoint(),expected)
    def test_no_future_child_at_intermediate_availability_and_primary_reset(self):
        d,_=self.driver();g=d.contexts
        def visible(time,at):return [v.snapshot() for v in g.advance_available(time,at).values()]
        self.assertEqual(visible(0,0),[()])
        early=g.advance_available(0,60000);self.assertEqual([v.snapshot() for v in early.values()],[(1.,)])
        self.assertEqual(visible(0,239999),[(1.,2.,3.)])
        self.assertEqual(visible(0,240000),[(1.,2.,3.,4.)])
        self.assertEqual([v.snapshot() for v in early.values()],[(1.,)])
        self.assertEqual(visible(240000,240000),[()]);g.close()
    def test_unavailable_future_mutation_preserves_prefix(self):
        a,b=data();changed=replace(b,bars=(*b.bars[:8],*(replace(x,close=999.,high=999.) for x in b.bars[8:])))
        self.assertEqual(self.outputs()[:2],self.outputs(datasets=(a,changed))[:2])
        future=replace(b,bars=(*b.bars,*(replace(x,time=x.time+960000) for x in b.bars)))
        text=source('a=request.security_lower_tf("TEST:LOWER","1",close,calc_bars_count=8)\nplot(array.sum(a))')
        self.assertEqual(self.outputs(text),self.outputs(text,datasets=(a,future)))
        short=replace(a,bars=a.bars[:2])
        self.assertEqual(self.outputs(text,datasets=(short,b)),[10.,26.])
    def test_invalid_timeframe_explicit_empty_or_error(self):
        a,b=data();b=replace(b,timeframe='8',bars=tuple(replace(x,time=i*480000) for i,x in enumerate(b.bars)))
        text=source('a=request.security_lower_tf("TEST:LOWER","8",close,ignore_invalid_timeframe=true)\nplot(array.size(a))')
        self.assertEqual(self.outputs(text,datasets=(a,b)),[0]*4)
        with self.assertRaises(OpenPineError):self.driver(text.replace('true','false'),datasets=(a,b))
    def test_unsupported_options_effects_dynamic_local_and_forged_ir_rejected(self):
        for extra in (',ignore_invalid_symbol=true',',currency="USD"',',calc_bars_count=0',',calc_bars_count=-1'):
            with self.assertRaises(OpenPineError):self.session.compile(source('a=request.security_lower_tf("TEST:LOWER","1",close'+extra+')\nplot(array.size(a))'))
        for expr in ('strategy.equity','array.from(1)','request.security("TEST:LOWER","1",close)'):
            with self.assertRaises(OpenPineError):self.session.compile(source('a=request.security_lower_tf("TEST:LOWER","1",'+expr+')\nplot(array.size(a))'))
        p=self.session.compile(source());node=request_nodes(p)[0]
        bad=replace(node,value_type=ValueType('array.int','series'))
        store=p.statements[1];forged=replace(p,statements=(p.statements[0],replace(store,expression=bad),*p.statements[2:]))
        with self.assertRaises((OpenPineError,ValueError)):self.session.validate_program(forged)
    def test_corrupt_window_restore_poisoned_and_readers_closed(self):
        d,_=self.driver();saved=[];d.run(pause_after=2,checkpoint=lambda x:saved.append(loads(canonical(x.checkpoint()))))
        for field,value in (('times',[0]),('cursor',True),('parent_time',0)):
            state=copy.deepcopy(saved[-1]);row=next(iter(state['groups'].values()));row[field]=value
            other,_=self.driver()
            with self.assertRaises((ValueError,StopIteration)):other.restore(state)
            self.assertTrue(other.evaluation.closed)
    def test_replay_rejects_historical_magnifier_before_consuming_events(self):
        from packages.execution.realtime import ReplayEngine
        program=self.session.compile('//@version=6\nstrategy("Historical fills",use_bar_magnifier=true)\nplot(close)')
        with self.assertRaisesRegex(ValueError,'historical job'):ReplayEngine(self.session,program,(),'primary',Sink())

    def test_magnifier_fill_recalculation_and_every_point_restore_share_closed_windows(self):
        from test_magnifier import datasets,config,source as magnifier_source,BODY
        from test_observed_broker import Sink as TradingSink
        from packages.contracts.strategy_ir import broker_policy
        from packages.contracts.trading import BrokerSettings
        from packages.execution.driver import HistoricalDriver
        a,b=datasets();primary=MarketContext(a.context_id,a.symbol,a.timeframe,'uploaded-file.1')
        lower=MarketContext(b.context_id,b.symbol,b.timeframe,'uploaded-file.1')
        text=magnifier_source(6,'a=request.security_lower_tf("TEST:MAGNIFIER","1",close)\nvarip seen=-1\nif strategy.position_size>0 and seen==-1\n    seen:=array.size(a)\n'+BODY+'\nplot(array.sum(a))\nplot(seen)')
        p=self.session.compile(text);runtime=self.session.validate_program(p)
        def driver():
            out=TradingSink();src=MemorySource(a)
            contexts=self.session.context_scheduler(p,runtime,src,primary,((ContextBinding(lower,data_identity(b).sha256),MemorySource(b)),))
            d=HistoricalDriver(runtime.prepare(p,output_sink=out),src,contexts,broker_policy(p,BrokerSettings(tick_size='1')),
                magnifier=config(),lower_source=MemorySource(b))
            return d,out
        full,out=driver();full.run();expected=full.checkpoint()
        self.assertEqual([r[-1] for r in out.rows],[1000.,200.,-1,1020.,210.,0])
        for cut in range(1,17):
            first,prefix=driver();saved=[]
            first.run(checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))),pause=lambda:first.magnifier.observations>=cut)
            second,tail=driver();second.restore(saved[-1]);second.run()
            self.assertEqual(prefix.rows+tail.rows,out.rows,cut);self.assertEqual(prefix.events+tail.events,out.events,cut)
            self.assertEqual(second.checkpoint(),expected,cut)

    def test_independent_limits_for_same_dataset_create_budgeted_private_readers(self):
        from packages.contracts.lower_tf import io_budget
        text=source('a=request.security_lower_tf("TEST:LOWER","1",ta.cum(close),calc_bars_count=4)\nb=request.security_lower_tf("TEST:LOWER","1",ta.cum(close),calc_bars_count=8)\nplot(array.sum(a))\nplot(array.sum(b))')
        d,s=self.driver(text);self.assertEqual(len(d.contexts.groups),2);d.run()
        self.assertEqual([r[-1] for r in s.rows],[None,None,None,None,None,100.,140.,308.])
        p=self.session.compile(source('\n'.join(f'a{i}=request.security_lower_tf("TEST:LOWER","1",close,calc_bars_count={i+1})' for i in range(16))+'\nplot(array.size(a0))'))
        budget=io_budget(p,512,1);self.assertLessEqual(2*19*budget,512)

    def test_cancel_during_lower_warmup_poisoned(self):
        d,_=self.driver();calls=0
        def cancelled():
            nonlocal calls
            calls+=1;return calls>2
        with self.assertRaises(OpenPineError):d.run(cancelled=cancelled)
        self.assertTrue(d.evaluation.closed)
        self.assertTrue(all(g.iterator is None for g in d.contexts.groups.values()))

if __name__=='__main__':unittest.main()
