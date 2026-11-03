"""Signal colors, source positions, checkpoint atomicity and durable ranges."""
import copy
from dataclasses import replace
from pathlib import Path
from contextlib import nullcontext
from types import SimpleNamespace
import tempfile
import unittest
from packages.contracts import HISTORICAL_PROFILE_ID,BarFrame,RunResult,OpenPineError
from packages.contracts.codec import encode,decode
from packages.contracts.wire import loads,canonical
from packages.contracts.inputs import configured_result,settings_for
from packages.execution.session import Session
from packages.marketdata import read_bars,data_identity
from tools.markers_fixtures import routing,source

class VisualSignalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data=read_bars(Path('conformance/visual-signals/bars.json'))

    def run_body(self,body,v=6):return self.session.run(source(v,body),'signals.pine',data=self.data)
    def frames(self):return [BarFrame(self.data.context_id,i,b.time,b.close,b.open,b.high,b.low,b.volume) for i,b in enumerate(self.data.bars)]

    def test_all_versions_four_kinds_independent_text_colors_and_codec(self):
        for v in range(1,7):
            p,r=self.session.run(routing(v),'signals.pine',data=self.data)
            self.assertEqual(p.schema_version,'openpine.typed-ir.23')
            self.assertEqual([o.kind for o in r.outputs[1:]],['bgcolor','barcolor','plotshape','plotchar'])
            self.assertEqual(r.outputs[3].values,(None,None,1.,1.));self.assertEqual(r.outputs[3].offset,1)
            self.assertEqual(r.outputs[3].textcolors,('#FFFF0080',)*2+('#FFFFFFFF',)*2)
            self.assertEqual(decode(encode(p),type(p)),p);base=RunResult(r.identity,r.program_sha256,r.declaration,r.outputs,r.observation_count,r.data);self.assertEqual(decode(encode(base),RunResult),base)

    def test_absolute_numeric_zero_is_visible_and_bool_false_is_hidden(self):
        _,r=self.run_body('plotshape(0,location=location.absolute)\nplotshape(0)\nplotshape(false,location=location.absolute)\nplotchar(na)')
        self.assertEqual(r.outputs[0].values,(0.,)*4)
        for o in r.outputs[1:]:self.assertEqual(o.values,(None,)*4)

    def test_scope_qualifiers_version_boundaries_and_invalid_values(self):
        for v in range(1,7):
            for body in ('if close>20\n    barcolor(#123456)\nplot(close)','f()=>plotshape(close>20)\nplot(close)',
                         'barcolor(#123456,bar_index)'):
                with self.subTest(v=v,body=body):
                    with self.assertRaises(OpenPineError):self.run_body(body,v)
        for body in ('plotshape(true,text=close>20 ? "A" : "B")','s=input("small")\nplotchar(true,size=s)',
            'plotshape(true,style="invalid")','plotchar(true,char="AB")','plotshape(true,location="invalid")',
            'barcolor(#123456,offset=10001)','plotchar(true,text="'+('x'*4097)+'")','bgcolor(#123456,transp=40)'):
            with self.subTest(body=body[:70]):
                with self.assertRaises(OpenPineError):self.run_body(body)

    def test_every_bar_cut_restores_both_color_channels_and_metadata(self):
        for v in range(1,7):
            p,expected=self.session.run(routing(v),'signals.pine',data=self.data)
            for cut in (1,2,3,4):
                a=self.session.validate_program(p).prepare(p)
                for f in self.frames()[:cut]:a.step(f)
                saved=loads(canonical(a.checkpoint()));self.assertEqual(saved['schema_version'],'openpine.runtime-state.15')
                b=self.session.validate_program(p).prepare(p);b.restore(saved)
                for f in self.frames()[cut:]:b.step(f)
                actual=b.finish(data_identity(self.data));self.assertEqual(actual.outputs,expected.outputs);self.assertEqual(actual.observation_count,expected.observation_count)
                for key,value in (('offset',10001),('kind','barcolor'),('textcolors',['bad']*cut)):
                    bad=copy.deepcopy(saved);bad['presentations'][p.statements[-1].call_site_id][key]=value
                    fresh=self.session.validate_program(p).prepare(p)
                    with self.assertRaises(ValueError):fresh.restore(bad)
                    self.assertEqual(fresh.count,0)

    def test_saved_ir_cannot_change_default_or_nest_visual_effects(self):
        p,_=self.run_body('plotshape(close>20)');call=p.statements[-1]
        args=list(call.arguments);args[1]=replace(args[1],expression=replace(args[1].expression,value='tampered'))
        bad=replace(p,statements=(*p.statements[:-1],replace(call,arguments=tuple(args))))
        with self.assertRaises(OpenPineError):self.session.validate_program(bad)
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,schema_version='openpine.typed-ir.22'))

    def test_parquet_resume_range_has_text_colors_and_offset_halos(self):
        from packages.storage.objects import ObjectStore
        from packages.storage.results import ResultSink,series_rows
        from packages.execution.queries import ResultReader
        p,_=self.session.run(routing(6),'signals.pine',data=self.data)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);objects=ObjectStore(root/'objects');sink=ResultSink(objects,root/'one')
            a=self.session.validate_program(p).prepare(p,output_sink=sink)
            for f in self.frames()[:2]:a.step(f)
            state=loads(canonical(a.checkpoint()));prefix=loads(canonical(sink.checkpoint(2)));sink.close()
            tail=ResultSink(objects,root/'two');tail.restore(prefix,2)
            b=self.session.validate_program(p).prepare(p,output_sink=tail);b.restore(state)
            for f in self.frames()[2:]:b.step(f)
            summary=tail.finish({'observation_count':4});tail.close()
            rows=list(series_rows(objects,summary,1,3,presentation=True));self.assertEqual(len(rows),10)
            self.assertEqual(rows[3][3],{'value':None,'color':'#12345680','textcolor':'#FFFF0080'})
            src=SimpleNamespace(identity=SimpleNamespace(count=4),rows=lambda offset,limit:iter(self.data.bars[offset:offset+limit]))
            reader=ResultReader(None,objects,SimpleNamespace(source=lambda _:src),series_rows,SimpleNamespace(slot=lambda _:nullcontext()))
            reader.summary=lambda _:dict(status='executed',result=summary,snapshot_sha256='0'*64)
            chart=reader.chart('job',1,2)
            self.assertEqual(chart['outputs'][3]['textcolors'],['#FFFF0080','#FFFFFFFF'])
            self.assertEqual(chart['outputs'][3]['halo'],[{'index':-1,'value':None,'color':'#12345680','textcolor':'#FFFF0080'}])
            self.assertEqual(chart['outputs'][4]['halo'],[{'index':2,'value':40.,'color':'#12345680','textcolor':'#FFFFFFFF'}])
            bad=copy.deepcopy(prefix);bad['outputs'][3]['presentation']['offset']=10001
            with self.assertRaises(ValueError):ResultSink(objects,root/'bad').restore(bad,2)

    def test_realtime_every_event_cut_keeps_only_confirmed_outputs(self):
        from packages.execution.realtime import ReplayEngine
        from test_observed_broker import CONTEXT,tape
        class Sink:
            def __init__(self):self.rows=[]
            def emit(self,site,title,typ,value,index,time,*,presentation=None):self.rows.append((site,index,value,presentation))
        p=self.session.compile(source(6,'bgcolor(close>100 ? #FF000080 : na)\nplotshape(close>100,text="BUY",textcolor=close>101 ? #FFFFFFFF : #FFFF0080)'))
        def engine():
            sink=Sink();return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink),sink
        events=tape();full,out=engine()
        for e in events:full.apply(e)
        for cut in range(len(events)+1):
            a,left=engine()
            for e in events[:cut]:a.apply(e)
            saved=loads(canonical(a.checkpoint()));b,right=engine();b.restore(saved)
            for e in events[cut:]:b.apply(e)
            self.assertEqual(left.rows+right.rows,out.rows);self.assertEqual(b.checkpoint(),full.checkpoint())

    def test_strategy_recalculation_every_cut_keeps_signal_identity(self):
        from packages.execution.realtime import ReplayEngine
        from packages.contracts.strategy_cycle import PROFILE
        from packages.contracts.trading import BrokerSettings
        from test_strategy_cycle import source
        from test_observed_broker import CONTEXT,tape,Sink as BaseSink
        class Sink(BaseSink):
            def emit(self,site,title,typ,value,index,time,*,presentation=None):self.rows.append((site,index,value,presentation))
        text=source(6,'if bar_index==0 and strategy.position_size==0\n    strategy.entry("L",LONG)\nbgcolor(strategy.position_size>0 ? #12345680 : na)\nplotshape(strategy.position_size>0,text="OPEN")',tick=True)
        p=self.session.compile(text)
        def engine():
            sink=Sink();return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink,broker_settings=BrokerSettings(tick_size='1'),calculation_profile=PROFILE),sink
        events=tape();full,out=engine()
        for e in events:full.apply(e)
        self.assertGreater(full.calculation.cycle.count,full.evaluation.count)
        for cut in range(len(events)+1):
            a,left=engine()
            for e in events[:cut]:a.apply(e)
            b,right=engine();b.restore(loads(canonical(a.checkpoint())))
            for e in events[cut:]:b.apply(e)
            self.assertEqual(left.rows+right.rows,out.rows)
            self.assertEqual(left.events+right.events,out.events)
            self.assertEqual(b.evaluation.checkpoint(),full.evaluation.checkpoint())


    def test_new_schema_composes_with_enum_inputs_and_reference_heap(self):
        body='enum Side\n    left\n    right\ntype N\n    color c=#12345680\ns=input.enum(Side.left)\nc=input.color(#12345680)\nvar n=N.new(c)\nvar g=matrix.new<N>(1,1,n)\nvar m=map.new<string,N>()\nm.put("n",g.get(0,0))\na=plot(close)\nb=plot(close+2)\nfill(a,b,c)\nplotshape(close>20,color=s==Side.left ? m.get("n").c : c)'
        for v in (5,6):
            p,expected=self.run_body(body,v)
            a=self.session.validate_program(p).prepare(p)
            for f in self.frames()[:2]:a.step(f)
            saved=loads(canonical(a.checkpoint()))
            self.assertIn('heap',saved)
            b=self.session.validate_program(p).prepare(p);b.restore(saved)
            for f in self.frames()[2:]:b.step(f)
            self.assertEqual(configured_result(b.finish(data_identity(self.data)),settings_for(p)),expected)

if __name__=='__main__':unittest.main()
