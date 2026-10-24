"""Compiler admission, actual historical checkpoints and observed rollback."""
import copy
from dataclasses import replace
import unittest
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.driver import MemorySource,HistoricalDriver
from packages.marketdata import from_payload,data_identity
from packages.execution.realtime import ReplayEngine
from tools.signal_fixtures import source,VALUES,VOLUMES,prepare
from test_realtime import Sink,tape,PRIMARY


def dataset():return from_payload(loads(prepare()['conformance/signals/bars.json']))


def body(v):
    p='' if v<5 else 'ta.';summ='sum' if v<5 else 'math.sum'
    return '\n'.join('plot('+x+')' for x in (p+'barssince(close>3)',p+'valuewhen(close>3,close,1)',p+'cum(volume)',summ+'(close,3)',p+'wma(close,3)',p+'vwma(close,3)',p+'mom(close,2)',p+'roc(close,2)',p+'highestbars(close,3)',p+'lowestbars(close,3)',p+'rising(close,2) ? 1:0',p+'falling(close,2) ? 1:0'))


class SignalStateTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)
    def driver(self,p):return HistoricalDriver(self.session.validate_program(p).prepare(p),MemorySource(dataset()))

    def test_all_versions_resume_every_boundary_for_all_twelve_operations(self):
        for v in range(1,7):
            p=self.session.compile(source(v,body(v)));expected=self.session.execute(p,data=dataset())
            for cut in (1,2,3,7):
                a=self.driver(p);saved=[];a.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                b=self.driver(p);b.restore(saved[0]);b.run();self.assertEqual(b.finish(data_identity(dataset())),expected)

    def test_forged_kernel_shape_and_kind_rejected_before_assignment(self):
        p=self.session.compile(source(6,body(6)));a=self.driver(p);saved=[];a.run(pause_after=3,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
        for row in range(len(saved[0]['states'])):
            for mutation in ('kind','calls','unknown'):
                bad=copy.deepcopy(saved[0]);item=bad['states'][row]
                if mutation=='kind':item['kind']='wma' if item['kind']!='wma' else 'sum'
                elif mutation=='calls':item['value']['calls']=False
                else:item['value']['unknown']=0
                b=self.driver(p)
                with self.assertRaises(ValueError):b.restore(bad)
                self.assertEqual(b.evaluation.count,0)

    def test_observed_previews_do_not_commit_any_signal_state(self):
        for v in (1,6):
            p=self.session.compile(source(v,body(v)));sink=Sink();engine=ReplayEngine(self.session,p,(PRIMARY,),PRIMARY.context_id,sink)
            events=list(tape(5))
            for event in events:
                before=canonical(engine.evaluation.checkpoint()) if engine.evaluation.count else None
                engine.apply(event)
                if event.kind!='close' and before:self.assertEqual(canonical(engine.evaluation.checkpoint()),before)
            for cut in (1,4,8,len(events)):
                a=ReplayEngine(self.session,p,(PRIMARY,),PRIMARY.context_id,Sink())
                for event in events[:cut]:a.apply(event)
                b=ReplayEngine(self.session,p,(PRIMARY,),PRIMARY.context_id,Sink());b.restore(loads(canonical(a.checkpoint())))
                for event in events[cut:]:b.apply(event)
                self.assertEqual(canonical(b.checkpoint()),canonical(engine.checkpoint()))

    def test_written_udf_calls_and_loop_iterations_have_explicit_ownership(self):
        s='''//@version=6
indicator("owner")
remember(x) =>
    ta.valuewhen(true,x,0)
a=remember(close)
b=remember(100.0)
var float total=0
for i=0 to 2
    total:=ta.cum(1.0)
plot(a)
plot(b)
plot(total)
'''
        p,r=self.session.run(s,data=dataset());self.assertEqual(r.outputs[0].values,tuple(VALUES));self.assertEqual(r.outputs[1].values,(100.,)*9)
        self.assertEqual(r.outputs[2].values,tuple(float(3*(i+1)) for i in range(9)))
        a=self.driver(p);saved=[];a.run(pause_after=3,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))));b=self.driver(p);b.restore(saved[0]);b.run();self.assertEqual(b.finish(data_identity(dataset())),r)

    def test_v5_strict_and_v6_lazy_logic_keep_distinct_event_histories(self):
        for v in (5,6):
            _,r=self.session.run(source(v,'event=bar_index%2==0 and ta.barssince(true)==0\nplot(ta.barssince(event))'),data=dataset())
            self.assertEqual(r.outputs[0].values,(0,1,0,1,0,1,0,1,0))
            p=self.session.compile(source(v,'x=bar_index%2==0 and ta.rising(close,2)\nplot(x ? 1:0)'))
            d=self.driver(p);d.run();states=list(d.evaluation.states.values());self.assertEqual(states[0].calls,9 if v==5 else 5)

    def test_forged_implicit_volume_and_result_type_are_rejected(self):
        p=self.session.compile(source(6,'plot(ta.vwma(close,2))'))
        plot=p.statements[-1];call=plot.arguments[0].expression
        bad=replace(call,arguments=(replace(call.arguments[0],expression=call.arguments[1].expression),*call.arguments[1:]))
        program=replace(p,statements=(*p.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=bad),*plot.arguments[1:]))))
        with self.assertRaises(OpenPineError) as caught:self.session.validate_program(program)
        self.assertEqual(caught.exception.diagnostic.code,'IR_SIGNAL_INPUT')
        from packages.contracts import ValueType
        p=self.session.compile(source(6,'plot(ta.valuewhen(true,bar_index,0))'));plot=p.statements[-1];call=plot.arguments[0].expression
        bad=replace(call,value_type=ValueType('float','series'))
        program=replace(p,statements=(*p.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=bad),*plot.arguments[1:]))))
        with self.assertRaises(OpenPineError):self.session.validate_program(program)


if __name__=='__main__':unittest.main()
