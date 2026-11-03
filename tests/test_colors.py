"""Independent color values, version boundaries, durable state and row transport."""
import copy
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,ProgramIR,BarFrame,ValueType
from packages.contracts.codec import encode,decode
from packages.contracts.colors import invoke,valid,literal
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.marketdata import read_bars,data_identity
from packages.compiler.semantic.specification import ROOT

class ColorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data=read_bars(ROOT/'conformance/functions/fixed.json')

    def source(self,body,v=6):return f'//@version={v}\n'+('study' if v<5 else 'indicator')+'("Colors")\n'+body+'\n'
    def run_body(self,body,v=6):return self.session.run(self.source(body,v),'colors.pine',data=self.data)
    def reject(self,body,v=6,code=None):
        with self.assertRaises(OpenPineError) as e:self.run_body(body,v)
        if code:self.assertEqual(e.exception.diagnostic.code,code)

    def test_versioned_palette_and_dynamic_plot(self):
        for v in range(1,7):
            color='red' if v<4 else 'color.red'
            p,r=self.run_body('c=close>20 ? '+color+' : #00ff00\nplot(close,"Price",c,2)',v)
            self.assertEqual(p.schema_version,'openpine.typed-ir.21')
            self.assertEqual(r.outputs[0].colors,('#00FF00FF',)*2+(('#F23645FF' if v==6 else '#FF5252FF'),)*2)
            self.assertEqual(r.outputs[0].values,(10.,20.,30.,40.))
            self.assertEqual(decode(encode(p),ProgramIR),p)
            self.assertEqual((r.outputs[0].linewidth,r.outputs[0].style),(2,'line'))

    def test_legacy_plot_wire_unchanged(self):
        for v in range(1,7):
            p,r=self.run_body('plot(close)',v)
            self.assertEqual(p.schema_version,'openpine.typed-ir.5')
            self.assertEqual(set(encode(r.outputs[0])),{'$type','output_id','title','value_type','values'})

    def test_palette_constants_inside_functions_all_versions(self):
        from tools.colors_fixtures import routing
        for v in range(1,7):
            _,r=self.session.run(routing(v),'colors-routing.pine',data=self.data)
            self.assertEqual(r.outputs[0].values,(10.,30.,60.,100.))
            self.assertEqual(r.outputs[0].colors,('#00FF00FF',)*2+(('#F23645FF' if v==6 else '#FF5252FF'),)*2)

    def test_color_is_distinct_from_string_and_number(self):
        for body in ('plot(color.red)','int c=color.red\nplot(c)','color c="#FF0000FF"\nplot(1)',
            'c=color.red+color.blue\nplot(1)','plot(color.red<color.blue ? 1 : 0)',
            'if color.red\n    x=1\nplot(1)'):
            self.reject(body)

    def test_literals_and_missing(self):
        for v in range(1,7):
            _,r=self.run_body('color c=na\na=#abcdef\nplot(na(c) ? 1 : 0)\nplot(a==#ABCDEFFF ? 1 : 0)',v)
            self.assertEqual([o.values for o in r.outputs],[(1,)*4,(1,)*4])
            self.reject('c=#12345\nplot(1)',v,'COLOR_LITERAL')
            self.reject('c=#123456789\nplot(1)',v,'COLOR_LITERAL')
        self.assertEqual(literal('#12345678'),'#12345678')
        self.assertFalse(valid('#abcdef00'))

    def test_functions_channels_and_transparency(self):
        for v in (4,5,6):
            _,r=self.run_body('c=color.rgb(10,20,30,50)\nplot(color.r(c))\nplot(color.g(c))\nplot(color.b(c))\nplot(color.t(color.new(c,100)))\nplot(close,color=c)',v)
            self.assertEqual([o.values for o in r.outputs[:4]],[(10.,)*4,(20.,)*4,(30.,)*4,(100.,)*4])
            self.assertEqual(r.outputs[-1].colors,('#0A141E80',)*4)
            self.assertEqual(invoke('colors.rgb',(1.5,2.5,3.5,0)),'#020304FF')
            self.reject('plot(color.r(color.rgb(256,0,0)))',v,'COLOR_RANGE')
            self.reject('plot(close,color=color.new(color.red,-1))',v,'COLOR_RANGE')

    def test_function_availability(self):
        for v in (1,2,3):self.reject('c=color.rgb(1,2,3)\nplot(1)',v)
        for v in (2,3):
            _,r=self.run_body('c=color(red,100)\nplot(close,"",c)',v)
            self.assertEqual(r.outputs[0].colors,('#FF525200',)*4)
        self.reject('c=color(red,100)\nplot(1)',1)
        for v in (4,5,6):
            _,r=self.run_body('color c=color(na)\nplot(na(c) ? 1 : 0)',v)
            self.assertEqual(r.outputs[0].values,(1,)*4)

    def test_gradient_manual_rgba_and_missing_endpoints(self):
        for v in (4,5,6):
            _,r=self.run_body('c=color.from_gradient(close,10,30,#000000,#FFFFFF)\nplot(close,color=c)',v)
            self.assertEqual(r.outputs[0].colors,('#000000FF','#808080FF','#FFFFFFFF','#FFFFFFFF'))
        self.assertEqual(invoke('colors.gradient',(0,0,10,None,'#123456FF')),'#12345600')
        self.assertEqual(invoke('colors.gradient',(10,0,10,None,'#123456FF')),'#123456FF')
        self.assertIsNone(invoke('colors.gradient',(None,0,10,'#000000FF','#FFFFFFFF')))
        with self.assertRaises(ValueError):invoke('colors.gradient',(1,2,2,'#000000FF','#FFFFFFFF'))

    def test_transparency_half_byte_and_large_integer_gradient_precision(self):
        for t,alpha in ((10,'E6'),(30,'B3'),(50,'80'),(70,'4D'),(90,'1A')):
            self.assertEqual(invoke('colors.new',('#123456FF',t)),'#123456'+alpha)
            self.assertEqual(invoke('colors.rgb',(18,52,86,t)),'#123456'+alpha)
        self.assertEqual(invoke('colors.gradient',(2**53+1,2**53,2**53+2,'#000000FF','#FFFFFFFF')),'#808080FF')
        self.assertEqual(invoke('colors.gradient',(0.,-1e308,1e308,'#000000FF','#FFFFFFFF')),'#808080FF')

    def test_mixed_gradient_width_and_subnormal_float_inputs_stay_admitted(self):
        self.assertEqual(invoke('colors.gradient',(2**60+30,float(2**60),2**60+60,'#000000FF','#FFFFFFFF')),'#808080FF')
        self.assertEqual(invoke('colors.gradient',(5e-324,0.,1e-323,'#000000FF','#FFFFFFFF')),'#808080FF')
        # Every admitted value below lies strictly between the exact integer
        # boundaries; mixed promotion must not collapse the interval or raise.
        for power in (53,54,60,62):
            for middle in (1,3,30,60):
                base=2**power
                color=invoke('colors.gradient',(base+middle,float(base),base+2*middle,'#000000FF','#FFFFFFFF'))
                self.assertEqual(color,'#808080FF')

    def test_udf_branch_tuple_history_and_loops(self):
        for v in range(1,7):
            body='pair(x)=>[x,close]\n[c,n]=pair(close>20 ? #FF0000 : #00FF00)\nplot(n,"",c)\nplot(bar_index>0 and c[1]==#00FF00 ? 1 : 0)'
            _,r=self.run_body(body,v)
            self.assertEqual(r.outputs[0].colors,('#00FF00FF',)*2+('#FF0000FF',)*2)
            self.assertEqual(r.outputs[1].values,(0,1,1,0))
            if v>=2:
                _,r=self.run_body('c=for i=0 to 2\n    i==2 ? #FFFFFF : #000000\nplot(close,"",c)',v)
                self.assertEqual(r.outputs[0].colors,('#FFFFFFFF',)*4)

    def test_arrays_copy_slice_history(self):
        for v in (4,5,6):
            _,r=self.run_body('a=array.new_color(1,#000000)\narray.set(a,0,close>20 ? #FFFFFF : #000000)\nb=array.copy(a)\narray.set(a,0,#123456)\nc=array.get(b,0)\nplot(close,color=c)\nplot(bar_index>0 ? color.r(c[1]) : -1)',v)
            self.assertEqual(r.outputs[0].colors,('#000000FF',)*2+('#FFFFFFFF',)*2)
            self.assertEqual(r.outputs[1].values,(-1.,0.,0.,255.))

    def test_udt_map_matrix_and_methods(self):
        body='''type N
    color c=color.red
method shade(N self,color x)=>
    self.c:=x
    self.c
n=N.new()
var m=map.new<color,color>()
m.put(color.red,n.shade(#123456))
grid=matrix.new<color>(1,1,m.get(color.red))
a=array.from(grid.get(0,0))
[c,x]=[a.get(0),close]
plot(x,color=c)'''
        for v in (5,6):
            p,r=self.run_body(body,v)
            self.assertEqual(r.outputs[0].colors,('#123456FF',)*4)
            self.assertEqual(decode(encode(p),ProgramIR),p)

    def test_const_plot_parameters_and_legacy_transp(self):
        for v in range(1,7):
            color='red' if v<4 else 'color.red';style='histogram' if v<4 else 'plot.style_histogram'
            _,r=self.run_body(f'plot(close,"",{color},2,{style})',v)
            self.assertEqual(r.outputs[0].style,'histogram')
            self.reject(f'plot(close,"",{color},bar_index+1)',v,'ARGUMENT_QUALIFIER')
            if v<6:
                transparency=',100' if v<3 else ',transp=100'
                _,r=self.run_body(f'plot(close,"",{color},2,{style}'+transparency+')',v)
                self.assertTrue(all(c.endswith('00') for c in r.outputs[0].colors))
            else:
                self.reject('plot(close,color=color.red,transp=50)',v,'UNKNOWN_ARGUMENT')
                self.reject('plot(close,color=color.red,linewidth=0)',v,'COLOR_RANGE')

    def test_later_positional_arguments_cannot_change_pine_meaning(self):
        for v in (3,4,5,6):
            c='red' if v<4 else 'color.red';style='line' if v<4 else 'plot.style_line'
            self.reject(f'plot(close,"",{c},1,{style},50)',v,'PLOT_POSITIONAL_PROFILE')

    def test_color_overloads_defaults_and_primitive_receiver(self):
        for v in (5,6):
            body='''choose(color x)=>color.r(x)
choose(int x)=>float(x+1)
shade(color x=#123456)=>x
method fade(color self)=>color.new(self,100)
plot(choose(shade()))
plot(choose(2))
c=shade()
plot(close,color=c.fade())'''
            _,r=self.run_body(body,v)
            self.assertEqual([o.values for o in r.outputs[:2]],[(18.,)*4,(3.,)*4])
            self.assertEqual(r.outputs[2].colors,('#12345600',)*4)

    def test_color_switch_empty_loop_and_versioned_palette_components(self):
        for v in (5,6):
            _,r=self.run_body('c=switch\n    close>20=>color.red\n    =>color.teal\nplot(color.r(c))\nplot(color.g(color.yellow))',v)
            self.assertEqual(r.outputs[0].values,(0.,0.,255.,255.) if v==5 else (8.,8.,242.,242.))
            self.assertEqual(r.outputs[1].values,((235.,)*4 if v==5 else (216.,)*4))

    def test_restore_every_bar_and_corrupt_tokens(self):
        body='var color c=na\nc:=close>20 ? #FFFFFF : #000000\nplot(close,color=c)\nplot(na(c[1]) ? -1 : color.r(c[1]))'
        p,full=self.run_body(body)
        frames=[BarFrame(self.data.context_id,i,b.time,b.close,b.open,b.high,b.low,b.volume) for i,b in enumerate(self.data.bars)]
        for cut in (1,2,3):
            e=self.session.validate_program(p).prepare(p)
            for f in frames[:cut]:e.step(f)
            state=loads(canonical(e.checkpoint()))
            self.assertEqual(state['schema_version'],'openpine.runtime-state.13')
            restored=self.session.validate_program(p).prepare(p);restored.restore(state)
            for f in frames[cut:]:restored.step(f)
            self.assertEqual(restored.finish(data_identity(self.data)),full)
            bad=copy.deepcopy(state);key=next(iter(bad['persistent']));bad['persistent'][key]='#bad'
            fresh=self.session.validate_program(p).prepare(p)
            with self.assertRaises(ValueError):fresh.restore(bad)

    def test_heap_checkpoint_all_containers(self):
        for v in (5,6):
            body='type N\n    color c=#123456\nvarip a=array.new_color(1,#ABCDEF)\nvar m=map.new<color,color>()\nm.put(#123456,a.get(0))\nvar grid=matrix.new<color>(1,1,#112233)\nvar n=N.new()\nplot(close,color=m.get(n.c))\nplot(color.r(grid.get(0,0)))'
            p,_=self.run_body(body,v);e=self.session.validate_program(p).prepare(p)
            b=self.data.bars[0];e.step(BarFrame(self.data.context_id,0,b.time,b.close,b.open,b.high,b.low,b.volume))
            raw=loads(canonical(e.checkpoint()));fresh=self.session.validate_program(p).prepare(p);fresh.restore(raw)
            self.assertEqual(canonical(fresh.checkpoint()),canonical(raw))

    def test_realtime_preview_and_every_event_cut(self):
        from packages.execution.realtime import ReplayEngine
        from test_observed_broker import CONTEXT,tape
        class Sink:
            def __init__(self):self.rows=[]
            def emit(self,site,title,typ,value,index,time,*,presentation=None):self.rows.append((index,value,presentation))
        p=self.session.compile(self.source('varip color c=#000000\nc:=close>100 ? #FFFFFF : #000000\nplot(close,color=c)'))
        def engine():
            sink=Sink();return ReplayEngine(self.session,p,(CONTEXT,),'primary',sink),sink
        events=tape();full,out=engine()
        for event in events:full.apply(event)
        self.assertEqual(len(out.rows),2)
        for cut in range(len(events)+1):
            a,left=engine()
            for event in events[:cut]:a.apply(event)
            b,right=engine();b.restore(loads(canonical(a.checkpoint())))
            for event in events[cut:]:b.apply(event)
            self.assertEqual(left.rows+right.rows,out.rows)
            self.assertEqual(canonical(b.evaluation.checkpoint()),canonical(full.evaluation.checkpoint()))

    def test_parquet_prefix_restore_numeric_reads_and_chart_range(self):
        from contextlib import nullcontext
        from types import SimpleNamespace
        from packages.storage.objects import ObjectStore
        from packages.storage.results import ResultSink,series_rows
        from packages.execution.queries import ResultReader
        p,_=self.run_body('plot(close,color=close>20 ? #FF0000 : #00FF00,linewidth=3,style=plot.style_linebr)')
        with tempfile.TemporaryDirectory() as temporary:
            root=Path(temporary);objects=ObjectStore(root/'objects');sink=ResultSink(objects,root/'first')
            e=self.session.validate_program(p).prepare(p,output_sink=sink)
            frames=[BarFrame(self.data.context_id,i,b.time,b.close,b.open,b.high,b.low,b.volume) for i,b in enumerate(self.data.bars)]
            for f in frames[:2]:e.step(f)
            runtime=loads(canonical(e.checkpoint()));prefix=loads(canonical(sink.checkpoint(2)));sink.close()
            tail=ResultSink(objects,root/'second');tail.restore(prefix,2)
            resumed=self.session.validate_program(p).prepare(p,output_sink=tail);resumed.restore(runtime)
            for f in frames[2:]:resumed.step(f)
            summary=tail.finish({'observation_count':4});tail.close()
            self.assertEqual([r[3] for r in series_rows(objects,summary)],[10.,20.,30.,40.])
            rows=list(series_rows(objects,summary,1,3,presentation=True))
            self.assertEqual([r[3] for r in rows],[{'value':20.,'color':'#00FF00FF'},{'value':30.,'color':'#FF0000FF'}])
            source=SimpleNamespace(identity=SimpleNamespace(count=4),rows=lambda offset,limit:iter(self.data.bars[offset:offset+limit]))
            reader=ResultReader(None,objects,SimpleNamespace(source=lambda _:source),series_rows,SimpleNamespace(slot=lambda _:nullcontext()))
            reader.summary=lambda _:dict(status='executed',result=summary,snapshot_sha256='0'*64)
            window=reader.chart('job',1,2)
            self.assertEqual(window['outputs'][0]['values'],[20.,30.])
            self.assertEqual(window['outputs'][0]['colors'],['#00FF00FF','#FF0000FF'])
            self.assertEqual(window['outputs'][0]['linewidth'],3)

    def test_strategy_calculation_passes_keep_presentation(self):
        for v in (5,6):
            text=f'//@version={v}\nstrategy("Colors",calc_on_order_fills=true)\nif bar_index==0\n    strategy.entry("L",strategy.long)\nplot(close,color=close>20 ? #FFFFFF : #000000)\n'
            data=replace(self.data,schema_version="openpine.historical-bars.2",bars=tuple(replace(b,open=b.close,high=b.close,low=b.close,volume=1.) for b in self.data.bars))
            p,r=self.session.run(text,'strategy-colors.pine',data=data)
            self.assertEqual(r.outputs[0].colors,('#000000FF',)*2+('#FFFFFFFF',)*2)

    def test_closed_default_and_schema_admission(self):
        from packages.contracts.context_ir import ir_nodes
        from packages.contracts import CallIR,LiteralIR
        p,_=self.run_body('plot(close,color=#123456)')
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p,schema_version='openpine.typed-ir.5'))
        call=next(n for n in p.statements if isinstance(n,CallIR) and n.operation=='colors.plot')
        args=list(call.arguments);args[3]=replace(args[3],expression=replace(args[3].expression,value=9))
        forged=replace(p,statements=tuple(replace(n,arguments=tuple(args)) if n is call else n for n in p.statements))
        with self.assertRaises(OpenPineError) as e:self.session.validate_program(forged)
        self.assertEqual(e.exception.diagnostic.code,'IR_COLOR_INPUT')

    def test_independent_color_conformance(self):
        from tools.conformance import run_cases
        from tools.colors_fixtures import fixtures
        cases=loads(fixtures()['cases.json'])['cases']
        report=run_cases(cases_path='conformance/colors/cases.json')
        self.assertEqual(report['executed_programs'],sum(c['expected']['status']=='executed' for c in cases))
        self.assertEqual(report['expected_rejections'],sum(c['expected']['status']=='rejected' for c in cases))

if __name__=='__main__':unittest.main()
