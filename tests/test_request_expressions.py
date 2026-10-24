"""Requested-context numeric programs, frozen inputs and joint rollback boundaries."""

from dataclasses import replace
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID,HistoricalBar,HistoricalData,OpenPineError
from packages.contracts.context_ir import request_nodes
from packages.contracts.contexts import MarketContext,ContextBinding
from packages.contracts.inputs import input_schema,settings_for
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical,loads
from packages.execution.contexts import expression_program
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.execution.realtime import ReplayEngine
from packages.execution.session import Session
from packages.marketdata import data_identity


PRIMARY=MarketContext('primary','TEST:EXPRESSIONS','1','uploaded-file.1')
HIGHER=MarketContext('higher','TEST:EXPRESSIONS','2','uploaded-file.1')
OHLCV=((10.,13.,8.,11.,100.),(11.,16.,10.,14.,120.),(14.,15.,9.,10.,90.),
       (10.,20.,9.,19.,150.),(19.,21.,16.,18.,180.),(18.,24.,17.,22.,200.))
DATA=HistoricalData('primary',PRIMARY.symbol,'1',tuple(HistoricalBar(i*60000,100.+i,99.+i,102.+i,98.+i,10.)
    for i in range(12)),schema_version='openpine.historical-bars.2')
REQUESTED=HistoricalData('higher',HIGHER.symbol,'2',tuple(HistoricalBar(i*120000,c,o,h,l,v)
    for i,(o,h,l,c,v) in enumerate(OHLCV)),schema_version='openpine.historical-bars.2')


def source(version,expression,*,inputs=False,first_plot=False,flags=''):
    declaration='indicator' if version>=5 else 'study';api='request.security' if version>=5 else 'security'
    header=f'//@version={version}\n{declaration}("expressions")\n'
    if inputs:
        make=lambda kind,value:f'input.{kind}({value})' if version>=5 else f'input({value})'
        header+='\n'.join(f'{name}={make(kind,value)}' for name,kind,value in (
            ('length','int','2'),('scale','float','1.0'),('src','source','close'),
            ('label','string','"unused"'),('enabled','bool','true')))+'\n'
    return header+('plot(1)\n' if first_plot else '')+f'plot({api}("TEST:EXPRESSIONS","2",{expression}{flags}))'


def mapped(values,version,gaps=False):
    if version<3:return tuple(value if not gaps or i%2==0 else None for i,value in enumerate(v for x in values for v in (x,x)))
    return tuple(None if i==0 or gaps and i%2==0 else values[(i-1)//2] for i in range(12))


class Sink:
    def __init__(self):self.rows=[]
    def emit(self,site,title,value_type,value,index,timestamp):self.rows.append((index,timestamp,site,value))


def events():
    rows=[]
    for context,data in ((PRIMARY,DATA),(HIGHER,REQUESTED)):
        duration=int(context.timeframe)*60000
        for bar in data.bars[:6 if context==PRIMARY else 3]:
            rows.append(MarketEvent(0,context.context_id,'open',bar.time,bar.time,0,
                bar.open,bar.open,bar.open,bar.open,0.))
            rows.append(MarketEvent(0,context.context_id,'update',bar.time,bar.time+duration//2,1,
                bar.open,max(bar.open,bar.close),min(bar.open,bar.close),bar.close,bar.volume/2))
            rows.append(MarketEvent(0,context.context_id,'close',bar.time,bar.time+duration,2,
                bar.open,bar.high,bar.low,bar.close,bar.volume))
    rows.sort(key=lambda e:(e.event_time,2 if e.kind=='open' else 1 if e.context_id=='primary' else 0))
    return tuple(replace(row,sequence=i) for i,row in enumerate(rows))


class RequestExpressionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def run_program(self,program,overrides=None,requested=REQUESTED):
        return self.session.execute(program,data=DATA,primary_context=PRIMARY,
            context_sources=((ContextBinding(HIGHER,'a'*64),MemorySource(requested)),),inputs=overrides)

    def driver(self,program,overrides=None):
        runtime=self.session.validate_program(program);data=MemorySource(DATA);settings=settings_for(program,overrides)
        contexts=self.session.context_scheduler(program,runtime,data,PRIMARY,
            ((ContextBinding(HIGHER,'a'*64),MemorySource(REQUESTED)),),settings=settings)
        return HistoricalDriver(runtime.prepare(program,settings=settings),data,contexts)

    def assert_values(self,actual,expected):
        self.assertEqual(len(actual),len(expected))
        for a,b in zip(actual,expected):
            if b is None:self.assertIsNone(a)
            else:self.assertAlmostEqual(a,b,places=11)

    def test_all_versions_ohlcv_arithmetic_and_default_visibility(self):
        independent=(10.5,11.2,16.9,7.,21.3,18.5)
        for version in range(1,7):
            with self.subTest(version=version):
                program=self.session.compile(source(version,'(high+low)/2+open-close+volume/100',first_plot=True))
                result=self.run_program(program)
                self.assert_values(result.outputs[1].values,mapped(independent,version))
                self.assertEqual(result.requests[0].historical_future,version<3)
                self.assertEqual(result.outputs[0].values,(1,)*12)

    def test_requested_constants_keep_versioned_integer_division(self):
        for version in range(1,7):
            program=self.session.compile(source(version,'7/2'))
            self.assert_values(self.run_program(program).outputs[0].values,mapped((3. if version<6 else 3.5,)*6,version))

    def test_all_versions_full_input_schema_source_override_and_private_state(self):
        independent=(None,None,22/3,8.5,28/3,65/6)
        for version in range(1,7):
            with self.subTest(version=version):
                ta='ta.' if version>=5 else ''
                text=source(version,f'{ta}sma(src,length)*scale',inputs=True)
                text=text.replace('length=', 'unused(x) => x+1\nlength=',1)
                program=self.session.compile(text)
                runtime=self.session.validate_program(program);node=request_nodes(program)[0]
                private=expression_program(program,node.arguments[2].expression,runtime,node.span)
                self.assertEqual(input_schema(private),input_schema(program))
                self.assertEqual(private.schema_version,'openpine.typed-ir.6')
                self.assertEqual(private.functions,())
                self.assertEqual(len(private.bindings),5)
                overrides={'length':3,'src':'high','scale':.5,'label':'kept','enabled':False}
                result=self.run_program(program,overrides)
                self.assert_values(result.outputs[0].values,mapped(independent,version))
                self.assertEqual(result.settings,settings_for(program,overrides))
                default=self.run_program(program)
                self.assertNotEqual(default.outputs[0].values,result.outputs[0].values)

    def test_history_and_two_requested_calls_are_context_local(self):
        for version in range(1,7):
            api='request.security' if version>=5 else 'security';ta='ta.' if version>=5 else ''
            body=source(version,'src[1]+high-low',inputs=True)
            body+=f'\nplot({api}("TEST:EXPRESSIONS","2",{ta}sma(low,2)))\nplot({ta}sma(close,2))'
            program=self.session.compile(body);result=self.run_program(program,{'src':'open'})
            self.assert_values(result.outputs[0].values,mapped((None,16.,17.,25.,15.,26.),version))
            self.assert_values(result.outputs[1].values,mapped((None,9.,9.5,9.,12.5,16.5),version))
            self.assert_values(result.outputs[2].values,(None,*[100.5+i for i in range(11)]))

    def test_all_versions_joint_checkpoint_at_every_primary_boundary(self):
        for version in range(1,7):
            ta='ta.' if version>=5 else ''
            program=self.session.compile(source(version,f'{ta}sma(src,length)+{ta}ema(close,2)',inputs=True))
            overrides={'src':'high','length':3};baseline=self.run_program(program,overrides)
            for cut in range(1,13):
                with self.subTest(version=version,cut=cut):
                    driver=self.driver(program,overrides);saved=[]
                    driver.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                    resumed=self.driver(program,overrides);resumed.restore(saved[0]);resumed.run()
                    self.assertEqual(resumed.finish(data_identity(DATA)),baseline)

    def test_future_changes_gaps_and_inactive_input_settings_identity(self):
        program=self.session.compile(source(6,'ta.sma(src,length)+volume',inputs=True,flags=',gaps=barmerge.gaps_on'))
        result=self.run_program(program)
        changed=replace(REQUESTED,bars=(*REQUESTED.bars[:-1],replace(REQUESTED.bars[-1],high=999.,close=888.)))
        second=self.run_program(program,requested=changed)
        self.assertEqual(result.outputs[0].values[:11],second.outputs[0].values[:11])
        self.assertNotEqual(result.outputs[0].values[-1],second.outputs[0].values[-1])
        self.assertTrue(all(result.outputs[0].values[i] is None for i in range(0,12,2)))
        first=self.driver(program);saved=[];first.run(pause_after=4,checkpoint=lambda d:saved.append(d.checkpoint()))
        for overrides in ({'length':3},{'src':'open'},{'label':'another'}):
            with self.assertRaisesRegex(ValueError,'settings'):self.driver(program,overrides).restore(saved[0])

    def test_pure_expression_admission_rejects_capture_effects_dynamic_inputs_and_excess_depth(self):
        bad=('bar_index','strategy.equity','request.security("TEST:EXPRESSIONS","2",close)',
             'close[length]')
        for expression in bad:
            with self.subTest(expression=expression),self.assertRaises(OpenPineError):
                self.session.compile(source(6,expression,inputs=True))
        for body in ('n=close\nn:=open\nplot(request.security("TEST:EXPRESSIONS","2",n))',
                     'var n=close\nplot(request.security("TEST:EXPRESSIONS","2",n))',
                     'n=input.int(2)\nn:=3\nplot(request.security("TEST:EXPRESSIONS","2",ta.sma(close,n)))'):
            with self.subTest(body=body),self.assertRaises(OpenPineError):self.session.compile('//@version=6\nindicator("bad")\n'+body)
        expression='close'
        for _ in range(18):expression='math.abs('+expression+')'
        with self.assertRaisesRegex(OpenPineError,'size/depth'):self.session.compile(source(6,expression))

    def test_frozen_ir_cannot_smuggle_primary_variables_or_forged_operations(self):
        program=self.session.compile(source(6,'close',inputs=True))
        request=request_nodes(program)[0];plot=program.statements[-1];reference=request.arguments[2].expression
        direct_input=program.statements[3].expression
        for bad in (direct_input,replace(reference,contract=replace(reference.contract,slot='bar_index')),
                    replace(request,operation='numeric.abs')):
            changed=replace(request,arguments=(*request.arguments[:2],replace(request.arguments[2],expression=bad),*request.arguments[3:]))
            corrupted=replace(program,statements=(*program.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=changed),*plot.arguments[1:]))))
            with self.assertRaises(OpenPineError):self.session.validate_program(corrupted)

    def test_all_versions_requested_technical_kernels_have_independent_numeric_expectations(self):
        expected={'tr':(5.,6.,6.,11.,5.,7.),'atr':(None,5.5,5.75,8.375,6.6875,6.84375),
                  'rma':(None,12.5,11.25,15.125,16.5625,19.28125),
                  'highest':(13.,16.,16.,20.,21.,24.),'lowest':(8.,8.,9.,9.,9.,16.)}
        for version in range(1,7):
            ta='ta.' if version>=5 else ''
            for name,values in expected.items():
                args={'tr':'true','atr':'length','rma':'src,length','highest':'high,length','lowest':'low,length'}[name]
                with self.subTest(version=version,name=name):
                    program=self.session.compile(source(version,f'{ta}{name}({args})',inputs=True))
                    self.assert_values(self.run_program(program).outputs[0].values,mapped(values,version))
            # Independent Wilder gain/loss ratios for highs 13,16,15,20,21,24.
            program=self.session.compile(source(version,f'{ta}rsi(high,length)',inputs=True))
            self.assert_values(self.run_program(program).outputs[0].values,mapped((None,None,75.,100*13/14,100*17/18,100*41/42),version))

    def test_strategy_without_primary_plot_uses_the_same_requested_evaluator(self):
        for version in range(1,7):
            api='request.security' if version>=5 else 'security';direction='true' if version==1 else 'strategy.long'
            program=self.session.compile(f'//@version={version}\nstrategy("request",default_qty_type=strategy.fixed)\nn=input(2)\nif bar_index==0\n    strategy.entry("L",{direction})\nx={api}("TEST:EXPRESSIONS","2",high+low+n)')
            result=self.run_program(program,{'n':3})
            self.assertEqual(result.observation_count,12)
            self.assertEqual(len(result.requests),1)

    def test_all_versions_replay_preview_restart_and_input_identity(self):
        tape=events()
        for version in range(1,7):
            ta='ta.' if version>=5 else ''
            program=self.session.compile(source(version,f'{ta}atr(length)+{ta}highest(src,length)+{ta}rsi(high,length)',inputs=True))
            settings=settings_for(program,{'src':'high','length':2,'label':'replay'})
            def engine(selected=settings):
                out=Sink();return ReplayEngine(self.session,program,(PRIMARY,HIGHER),'primary',out,settings=selected),out
            baseline,expected=engine()
            for event in tape:
                before={k:canonical(r['evaluation'].checkpoint()) for k,r in baseline.requests.items() if r['evaluation'].count}
                baseline.apply(event)
                if event.kind!='close':
                    for key,snapshot in before.items():self.assertEqual(canonical(baseline.requests[key]['evaluation'].checkpoint()),snapshot)
            for cut in range(1,len(tape)+1):
                with self.subTest(version=version,cut=cut):
                    first,prefix=engine()
                    for event in tape[:cut]:first.apply(event)
                    checkpoint=loads(canonical(first.checkpoint()));resumed,tail=engine();resumed.restore(checkpoint)
                    for event in tape[cut:]:resumed.apply(event)
                    self.assertEqual(prefix.rows+tail.rows,expected.rows)
                    self.assertEqual(resumed.status(),baseline.status())
            changed,_=engine(settings_for(program,{'src':'low','length':2,'label':'replay'}))
            with self.assertRaisesRegex(ValueError,'another program/context/policy'):changed.restore(baseline.checkpoint())


if __name__=='__main__':unittest.main()
