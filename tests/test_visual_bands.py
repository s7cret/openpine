"""Independent color input, visual identity, durable layers and rendering contracts."""
import copy
from dataclasses import asdict,replace
from pathlib import Path
import tempfile
import unittest
from packages.contracts import HISTORICAL_PROFILE_ID,BarFrame,RunResult,OpenPineError
from packages.contracts.codec import encode,decode
from packages.contracts.inputs import settings_for,configured_result
from packages.contracts.wire import loads,canonical
from packages.execution.session import Session
from packages.marketdata import read_bars,data_identity

class VisualBandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data=read_bars(Path('conformance/functions/fixed.json'))

    def source(self,body,v=6):return f'//@version={v}\n'+('indicator' if v>=5 else 'study')+'("Bands")\n'+body
    def run_body(self,body,v=6,inputs=None):return self.session.run(self.source(body,v),'bands.pine',data=self.data,inputs=inputs)
    def reject(self,body,v=6):
        with self.assertRaises(OpenPineError):self.run_body(body,v)

    def test_color_inputs_all_versions_defaults_overrides_and_qualifier(self):
        for v in range(1,4):
            self.reject('c=input(#12345680)\nplot(close,"",c)',v)
        for v in range(4,7):
            declaration='c=input.color(#12345680)' if v>=5 else 'c=input(#12345680,type='+('input.color' if v==4 else 'color')+')'
            p,r=self.run_body(declaration+'\nplot(close,"",c)',v)
            self.assertEqual(r.outputs[0].colors,('#12345680',)*4)
            self.assertEqual(p.bindings[0].value_type.base,'color')
            self.assertEqual(p.bindings[0].value_type.qualifier,'input')
            _,other=self.run_body(declaration+'\nplot(close,"",c)',v,{'c':'#aabbcc'})
            self.assertEqual(other.outputs[0].colors,('#AABBCCFF',)*4)
            self.assertNotEqual(r.settings['sha256'],other.settings['sha256'])
            self.assertEqual(settings_for(p,{'c':'#AABBCCff'}),settings_for(p,{'c':'#aabbcc'}))
            self.assertEqual(r.outputs[0].colors,('#12345680',)*4)

    def test_color_constructor_defaults_are_closed_and_missing_is_rejected(self):
        for v in (5,6):
            _,r=self.run_body('color GOLD=#123456\nc=input.color(color.new(GOLD,50))\nplot(close,color=c)',v)
            self.assertEqual(r.outputs[0].colors,('#12345680',)*4)
            for value in (None,1,'#xyz','red','#123456789'):
                with self.subTest(v=v,value=value):
                    with self.assertRaises(OpenPineError):self.run_body('c=input.color(#123456)\nplot(close,color=c)',v,{'c':value})
            for body in ('c=input.color(na)\nplot(1)','c=input.color("#123456")\nplot(1)',
                         'c=input.color(close>20 ? #123456 : #FFFFFF)\nplot(1)',
                         'c=input.color(#123456,options=[#123456])\nplot(1)'):
                self.reject(body,v)
        self.reject('c=input.color(#123456)\nplot(1)',4)

    def test_nz_color_transparent_default_and_numeric_behavior_are_separate(self):
        for v in range(1,7):
            _,r=self.run_body('color c=na\nplot(close,"",nz(c))\nplot(close,"",nz(c,#123456))\nplot(nz(close[1]))',v)
            self.assertEqual(r.outputs[0].colors,('#00000000',)*4)
            self.assertEqual(r.outputs[1].colors,('#123456FF',)*4)
            self.assertEqual(r.outputs[2].values,(0.,10.,20.,30.))
            self.reject('color c=na\nplot(close,"",nz(c,"#123456"))',v)

    def test_plot_references_hline_aliases_and_same_kind_fills(self):
        for v in range(1,7):
            p,r=self.run_body('a=plot(close)\nb=plot(close+1)\nc=a\nfill(c,b,#12345680)\nx=hline(10)\ny=hline(30)\nfill(x,y,#ABCDEF80)',v)
            self.assertEqual(p.schema_version,'openpine.typed-ir.22')
            self.assertEqual([o.kind for o in r.outputs],['plot','plot','fill','hline','hline','fill'])
            self.assertEqual(r.outputs[2].references,tuple(o.output_id for o in r.outputs[:2]))
            self.assertEqual(r.outputs[5].references,tuple(o.output_id for o in r.outputs[3:5]))
            self.assertEqual(r.outputs[2].colors,('#12345680',)*4)
            self.assertFalse(r.outputs[2].fillgaps)
            self.assertFalse(r.outputs[5].fillgaps)
            self.assertEqual(r.outputs[3].values,(10,)*4)
            self.assertEqual(decode(encode(r),RunResult),r)

    def test_reference_scope_history_arithmetic_and_mixed_endpoints_rejected(self):
        for body in ('p=plot(close)\nh=hline(1)\nfill(p,h,#123456)',
            'var p=plot(close)\nplot(1)','p=plot(close)\np:=p\nplot(1)',
            'p=plot(close)\nplot(p[1])','p=plot(close)\nplot(p+1)',
            'f()=>plot(close)\np=f()\nplot(1)',
            'if close>20\n    p=plot(close)\nplot(1)',
            'p=plot(close)\na=array.from(p)\nplot(1)',
            'fill(plot(close),plot(close+1),#123456)'):
            self.reject(body)

    def test_hline_const_input_price_color_and_styles(self):
        for v in (4,5,6):
            style='hline.style_dashed'
            _,r=self.run_body(f'h=hline(10,"Level",#123456,{style},2)',v)
            self.assertEqual((r.outputs[0].kind,r.outputs[0].style,r.outputs[0].linewidth),('hline','dashed',2))
            self.reject('h=hline(close)',v)
            self.reject('h=hline(10,color=close>20 ? #123456 : #FFFFFF)',v)
        self.reject('a=plot(close)\nb=plot(close+1)\nfill(a,b,#123456,"wrong fourth positional")')

    def frames(self):return [BarFrame(self.data.context_id,i,b.time,b.close,b.open,b.high,b.low,b.volume) for i,b in enumerate(self.data.bars)]

    def test_native_restore_every_cut_owns_exact_references(self):
        p,expected=self.run_body('a=plot(close)\nb=plot(close+2)\nfill(a,b,close>20 ? #FF000080 : #00FF0080)')
        frames=self.frames()
        for cut in (1,2,3,4):
            a=self.session.validate_program(p).prepare(p)
            for f in frames[:cut]:a.step(f)
            saved=loads(canonical(a.checkpoint()));self.assertEqual(saved['schema_version'],'openpine.runtime-state.14')
            b=self.session.validate_program(p).prepare(p);b.restore(saved)
            for f in frames[cut:]:b.step(f)
            self.assertEqual(b.finish(data_identity(self.data)),expected)
            bad=copy.deepcopy(saved);last=p.statements[-1].call_site_id
            bad['presentations'][last]['references'][0]='call:0:1'
            fresh=self.session.validate_program(p).prepare(p)
            with self.assertRaises(ValueError):fresh.restore(bad)
            self.assertEqual(fresh.count,0)

    def test_forged_ir_cannot_fabricate_handles_or_foreign_references(self):
        from packages.contracts import LiteralIR,ValueType
        p,_=self.run_body('a=plot(close)\nb=plot(close+2)\nfill(a,b,#123456)')
        first=p.statements[1]
        forged=replace(p,statements=(p.statements[0],replace(first,expression=LiteralIR('plot|call:0:1',ValueType('plot_id'),first.span)),*p.statements[2:]))
        with self.assertRaises(OpenPineError):self.session.validate_program(forged)

    def test_forged_persistent_hline_style_is_rejected_before_checkpoint(self):
        from packages.contracts import ValueType
        for v in range(1,7):
            style='hline.style_solid' if v>=4 else 'solid'
            p=self.session.compile(self.source('s='+style+'\nplot(close)',v))
            other='hline.style_dashed' if v>=4 else 'dashed'
            _,result=self.run_body(f'b=input(true)\ns=b ? {style} : {other}\nh=hline(10,"",#123456,s,1)',v)
            self.assertEqual(result.outputs[0].style,'solid')
            self.reject(f's=close>20 ? {style} : {other}\nplot(close)',v)
            for mode in ('var','varip'):
                binding=replace(p.bindings[0],mode=mode,value_type=ValueType('hline_style','series'))
                forged=replace(p,bindings=(binding,),statements=(p.statements[0],replace(p.statements[1],binding=binding),*p.statements[2:]))
                with self.assertRaises(OpenPineError) as error:self.session.validate_program(forged)
                self.assertEqual(error.exception.diagnostic.code,'IR_VISUAL_REFERENCE')

    def test_parquet_prefix_restore_preserves_layer_graph_and_ranges(self):
        from packages.storage.objects import ObjectStore
        from packages.storage.results import ResultSink,series_rows
        from packages.execution.queries import ResultReader
        from contextlib import nullcontext
        from types import SimpleNamespace
        p,_=self.run_body('a=plot(close)\nb=plot(close+2)\nfill(a,b,#12345680)')
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);objects=ObjectStore(root/'objects');sink=ResultSink(objects,root/'one')
            a=self.session.validate_program(p).prepare(p,output_sink=sink)
            for f in self.frames()[:2]:a.step(f)
            state=loads(canonical(a.checkpoint()));prefix=loads(canonical(sink.checkpoint(2)));sink.close()
            tail=ResultSink(objects,root/'two');tail.restore(prefix,2)
            b=self.session.validate_program(p).prepare(p,output_sink=tail);b.restore(state)
            for f in self.frames()[2:]:b.step(f)
            summary=tail.finish({'observation_count':4});tail.close()
            rows=list(series_rows(objects,summary,1,3,presentation=True));self.assertEqual(len(rows),6)
            self.assertEqual(rows[2][3],{'color':'#12345680','value':None})
            source=SimpleNamespace(identity=SimpleNamespace(count=4),rows=lambda offset,limit:iter(self.data.bars[offset:offset+limit]))
            reader=ResultReader(None,objects,SimpleNamespace(source=lambda _:source),series_rows,SimpleNamespace(slot=lambda _:nullcontext()))
            reader.summary=lambda _:dict(status='executed',result=summary,snapshot_sha256='0'*64)
            chart=reader.chart('job',1,2)
            self.assertEqual(chart['outputs'][2]['kind'],'fill')
            self.assertEqual(chart['outputs'][2]['references'],[o['output_id'] for o in chart['outputs'][:2]])
            self.assertEqual(chart['outputs'][2]['colors'],['#12345680']*2)
            bad=copy.deepcopy(prefix);bad['outputs'][2]['presentation']['references'][0]='call:0:1'
            with self.assertRaises(ValueError):ResultSink(objects,root/'bad').restore(bad,2)

    def test_realtime_every_event_cut_preserves_band_graph_and_preview(self):
        from packages.execution.realtime import ReplayEngine
        from test_observed_broker import CONTEXT,tape
        class Sink:
            def __init__(self):self.rows=[]
            def emit(self,site,title,typ,value,index,time,*,presentation=None):self.rows.append((site,index,value,presentation))
        p=self.session.compile(self.source('a=plot(close)\nb=plot(close+2)\nfill(a,b,close>100 ? #FF000080 : #00FF0080)'))
        def engine():
            sink=Sink();return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink),sink
        events=tape();full,out=engine()
        for e in events:full.apply(e)
        for cut in range(len(events)+1):
            a,left=engine()
            for e in events[:cut]:a.apply(e)
            b,right=engine();b.restore(loads(canonical(a.checkpoint())))
            for e in events[cut:]:b.apply(e)
            self.assertEqual(left.rows+right.rows,out.rows)
            self.assertEqual(canonical(b.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))

    def test_legacy_program_schemas_and_plain_plot_shape_unchanged(self):
        p,r=self.run_body('plot(close)');self.assertNotEqual(p.schema_version,'openpine.typed-ir.22')
        self.assertEqual(set(asdict(r.outputs[0])),{'output_id','title','value_type','values'})
        p,_=self.run_body('plot(close,color=#123456)');self.assertEqual(p.schema_version,'openpine.typed-ir.21')

    def test_input_level_gaps_transparency_and_closed_defaults(self):
        for v in (4,5,6):
            setting='c=input(#123456,type=input.color)' if v==4 else 'c=input.color(#123456)'
            p,r=self.run_body(setting+'\nn=input(12)\ng=false\nx=hline(n,color=c)\ny=hline(30)\nfill(x,y,c,fillgaps=g)',v)
            self.assertEqual(r.outputs[0].values,(12,)*4)
            self.assertFalse(r.outputs[2].fillgaps)
            self.assertEqual(r.outputs[2].colors,('#123456FF' if v==6 else '#1234561A',)*4)
            self.assertEqual(decode(encode(p),type(p)),p)
            self.reject('c=input(10)\nd=input(c)\nplot(1)',v)
            self.reject('g=input(false)\na=plot(close)\nb=plot(close+2)\nfill(a,b,#12345680,fillgaps=g)',v)
        self.reject('b=input(true)\nc=b ? #123456 : #FFFFFF\nh=hline(1,"",c)',3)
        self.reject('x=hline(1)\ny=hline(2)\nfill(x,y,#123456,transp=20)',6)
        for v in (1,2,3):self.reject('x=hline(1)\ny=hline(2)\nfill(x,y,#123456,fillgaps=false)',v)

    def test_strategy_recalculation_every_cut_keeps_layer_identity(self):
        from packages.execution.realtime import ReplayEngine
        from packages.contracts.strategy_cycle import PROFILE
        from packages.contracts.trading import BrokerSettings
        from test_strategy_cycle import source
        from test_observed_broker import CONTEXT,tape,Sink as BaseSink
        class Sink(BaseSink):
            def emit(self,site,title,typ,value,index,time,*,presentation=None):self.rows.append((site,index,value,presentation))
        text=source(6,'if bar_index==0 and strategy.position_size==0\n    strategy.entry("L",LONG)\na=plot(strategy.position_size)\nb=plot(strategy.position_size+2)\nfill(a,b,#12345680)',tick=True)
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

    def test_owned_examples_have_independent_rsi_and_ma_values(self):
        from tools.bands_fixtures import corpus_cases
        for case in corpus_cases(lambda p:{'path':p}):
            p,r=self.session.run(Path(case['files']['source']['path']).read_text(),data=self.data)
            self.assertEqual([list(o.values) for o in r.outputs],case['oracle']['outputs'],case['id'])

    def test_new_schema_composes_with_enum_inputs_and_reference_heap(self):
        body='enum Side\n    left\n    right\ntype N\n    color c=#12345680\ns=input.enum(Side.left)\nc=input.color(#12345680)\nvar n=N.new(c)\nvar g=matrix.new<N>(1,1,n)\nvar m=map.new<string,N>()\nm.put("n",g.get(0,0))\na=plot(close)\nb=plot(close+2)\nfill(a,b,s==Side.left ? m.get("n").c : c)'
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
