"""Private dependency graphs: independent arithmetic, ownership and replay."""
import copy
from dataclasses import replace
from fractions import Fraction
import unittest
import test_request_expressions as legacy
from packages.contracts import HISTORICAL_PROFILE_ID,OpenPineError,LoadIR,LiteralIR,ValueType
from packages.contracts.context_ir import request_nodes,request_expression
from packages.contracts.wire import canonical,loads
from packages.execution.session import Session
from packages.execution.contexts import expression_program
from packages.execution.realtime import ReplayEngine
from packages.marketdata import data_identity


def source(v,prefix,expression,flags=''):
    return legacy.source(v,expression,inputs=True,flags=flags).replace('plot(',prefix+'\nplot(',1)


class ContextGraphTests(unittest.TestCase):
    run_program=legacy.RequestExpressionTests.run_program
    driver=legacy.RequestExpressionTests.driver
    assert_values=legacy.RequestExpressionTests.assert_values

    @classmethod
    def setUpClass(cls):cls.session=Session(profile_id=HISTORICAL_PROFILE_ID)

    def test_all_versions_transitive_history_uses_requested_bars(self):
        mid=[Fraction(str(h+l))/2 for o,h,l,c,v in legacy.OHLCV]
        avg=[None,*[float((a+b)/2) for a,b in zip(mid,mid[1:])]]
        expected=[None,*avg[:-1]]
        for v in range(1,7):
            ta='ta.' if v>=5 else ''
            p=self.session.compile(source(v,f'typical=(high+low)/2\naverage={ta}sma(typical,length)','average[1]'))
            result=self.run_program(p)
            self.assert_values(result.outputs[0].values,legacy.mapped(expected,v))
            driver=self.driver(p)
            # The history under the request lives only in its private runtime.
            self.assertFalse(driver.evaluation.history.sources)
            self.assertTrue(next(iter(next(iter(driver.contexts.groups.values())).records.values()))[1].history.sources)

    def test_optional_function_and_switch_use_requested_values(self):
        for v in (5,6):
            prefix='f(x, multiplier=2) =>\n    switch\n        x>15 => x*(multiplier+1)\n        => x*multiplier'
            program=self.session.compile(source(v,prefix,'f(close)'))
            self.assert_values(self.run_program(program).outputs[0].values,legacy.mapped((22.,28.,20.,57.,54.,66.),v))
    def test_method_private_graph_and_resume_retain_one_selected_contract(self):
        for v in (5,6):
            program=self.session.compile(source(v,'method scale(int x)=>x+1\nmethod scale(float x,float factor=2)=>x*factor','close.scale()'))
            expected=self.run_program(program)
            self.assert_values(expected.outputs[0].values,legacy.mapped((22.,28.,20.,38.,36.,44.),v))
            private=next(iter(next(iter(self.driver(program).contexts.groups.values())).records.values()))[1].program
            self.assertEqual((private.schema_version,len(private.functions)),('openpine.typed-ir.13',1))
            for cut in range(1,12):
                first=self.driver(program);saved=[];first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                resumed=self.driver(program);resumed.restore(saved[0]);resumed.run()
                self.assertEqual(resumed.finish(data_identity(legacy.DATA)),expected)
    def test_overload_private_graph_and_checkpoint_keep_selected_definition(self):
        for v in (5,6):
            prefix='f(int x)=>x+1\nf(float x)=>x*2'
            program=self.session.compile(source(v,prefix,'f(close)'))
            self.assert_values(self.run_program(program).outputs[0].values,legacy.mapped((22.,28.,20.,38.,36.,44.),v))
            driver=self.driver(program)
            private=next(iter(next(iter(driver.contexts.groups.values())).records.values()))[1].program
            self.assertEqual(private.schema_version,'openpine.typed-ir.12')
            self.assertEqual(len(private.functions),1)
            expected=self.run_program(program).outputs[0].values
            for cut in range(1,len(legacy.DATA.bars)):
                first=self.driver(program);saved=[]
                first.run(pause_after=cut,checkpoint=lambda d:saved.append(loads(canonical(d.checkpoint()))))
                resumed=self.driver(program);resumed.restore(saved[0]);resumed.run()
                self.assert_values(resumed.finish(data_identity(legacy.DATA)).outputs[0].values,expected)

    def test_dependency_diamond_evaluates_stateful_initializer_once(self):
        for v in range(1,7):
            ta='ta.' if v>=5 else ''
            p=self.session.compile(source(v,f'base={ta}cum(volume)\na=base+1\nb=base-1','a+b+base'))
            self.assert_values(self.run_program(p).outputs[0].values,legacy.mapped((300.,660.,930.,1380.,1920.,2520.),v))
            d=self.driver(p);d.run()
            evaluation=next(iter(next(iter(d.contexts.groups.values())).records.values()))[1]
            self.assertEqual(len(evaluation.states),1)
            self.assertEqual(next(iter(evaluation.states.values())).calls,6)

    def test_bool_and_mixed_tuple_gaps_are_versioned(self):
        for v in range(1,7):
            missing=False if v==6 else None
            api='request.security' if v>=5 else 'security'
            flags=',true' if v<3 else ',gaps=barmerge.gaps_on'
            p=self.session.compile(source(v,'','close>open',flags).replace('plot(', 'condition=',1)[:-1]+'\nplot(condition ? 1 : 0)')
            driver=self.driver(p);driver.run()
            evaluation=next(iter(next(iter(driver.contexts.groups.values())).records.values()))[1]
            self.assertEqual(evaluation.program.statements[-1].expression.value_type.base,'bool')
            text=legacy.source(v,'close',inputs=True).rsplit('plot(',1)[0]
            text+=f'[price,event,count]={api}("TEST:EXPRESSIONS","2",[close,close>open,1]{flags})\nplot(price)\nplot(event ? 1 : 0)\nplot(count)'
            result=self.run_program(self.session.compile(text))
            self.assert_values(result.outputs[0].values,legacy.mapped((11.,14.,10.,19.,18.,22.),v,True))
            self.assertEqual(result.outputs[1].values,tuple(1 if x else 0 for x in legacy.mapped((True,True,False,True,False,True),v,True)))
            # Direct scheduler observation checks the missing type, not its ternary conversion.
            d=self.driver(self.session.compile(text))
            if v<3:d.contexts.advance(legacy.DATA.bars[0])
            row=d.contexts.advance(legacy.DATA.bars[1 if v<3 else 0])
            self.assertEqual(next(iter(row.values()))[1],missing)

    def test_conditionals_and_expression_history_are_lazy(self):
        for v in range(1,7):
            ta='ta.' if v>=5 else ''
            text=source(v,'',f'(close>open ? {ta}cum(volume) : -close)[1]')
            expected=(None,100.,220.,-10.,370.,-18.)
            self.assert_values(self.run_program(self.session.compile(text)).outputs[0].values,legacy.mapped(expected,v))

    def test_functions_capture_globals_and_keep_private_local_var(self):
        for v in range(1,7):
            prefix='scaled=close*scale\nf(x) =>\n    var float total=0\n    total:=total+x\n    if x>open\n        total+scaled\n    else\n        total-scaled'
            p=self.session.compile(source(v,prefix,'f(close)'))
            expected=(22.,39.,25.,73.,54.,116.)
            self.assert_values(self.run_program(p).outputs[0].values,legacy.mapped(expected,v))
            runtime=self.session.validate_program(p);node=request_nodes(p)[0]
            private=expression_program(p,node.arguments[2].expression,runtime,node.span)
            self.assertEqual(len(private.functions),1)
            self.assertTrue(any(b.mode=='var' for b in private.bindings))

    def test_two_written_calls_and_requests_have_separate_state(self):
        for v in range(1,7):
            api='request.security' if v>=5 else 'security'
            prefix='f(x) =>\n    var float n=0\n    n:=n+x\n    n\na=f(close)\nb=f(open)'
            text=source(v,prefix,'a+b')+f'\nplot({api}("TEST:EXPRESSIONS","2",f(volume)))'
            result=self.run_program(self.session.compile(text))
            self.assert_values(result.outputs[0].values,legacy.mapped((21.,46.,70.,99.,136.,176.),v))
            self.assert_values(result.outputs[1].values,legacy.mapped((100.,220.,310.,460.,640.,840.),v))

    def test_tuple_dependency_and_udf_tuple_keep_types(self):
        for v in range(1,7):
            ta='ta.' if v>=5 else '';api='request.security' if v>=5 else 'security'
            prefix=f'f(x) =>\n    [x,x>open]\n[fast,slow,delta]={ta}macd(close,2,3,2)'
            text=source(v,prefix,'fast')+f'\n[p,e]={api}("TEST:EXPRESSIONS","2",f(close))\nplot(p)\nplot(e ? 1 : 0)'
            result=self.run_program(self.session.compile(text))
            self.assert_values(result.outputs[1].values,legacy.mapped((11.,14.,10.,19.,18.,22.),v))
            self.assertEqual(result.outputs[2].values,tuple(1 if x else 0 for x in legacy.mapped((True,True,False,True,False,True),v)))

    def test_joint_restore_at_each_boundary_includes_private_locals(self):
        for v in (1,4,6):
            ta='ta.' if v>=5 else ''
            prefix=f'base=(high+low)/2\nf(x) =>\n    var float total=0\n    total:=total+x\n    total\nsmooth={ta}wma(base,length)'
            p=self.session.compile(source(v,prefix,'f(smooth)+smooth[1]'))
            baseline=self.run_program(p)
            for cut in range(1,13):
                d=self.driver(p);saved=[];d.run(pause_after=cut,checkpoint=lambda x:saved.append(loads(canonical(x.checkpoint()))))
                restored=self.driver(p);restored.restore(saved[0]);restored.run()
                self.assertEqual(restored.finish(data_identity(legacy.DATA)),baseline)

    def test_future_mutation_cannot_change_visible_prefix(self):
        p=self.session.compile(source(6,'src2=close*2\navg=ta.sma(src2,length)','avg[1]'))
        changed=replace(legacy.REQUESTED,bars=(*legacy.REQUESTED.bars[:-2],*(replace(b,high=1000.,close=999.) for b in legacy.REQUESTED.bars[-2:])))
        first=self.run_program(p);second=self.run_program(p,requested=changed)
        self.assertEqual(first.outputs[0].values[:11],second.outputs[0].values[:11])
        self.assertNotEqual(first.outputs[0].values[-1],second.outputs[0].values[-1])

    def test_mutable_transitive_and_effectful_captures_are_rejected(self):
        self.session.compile(source(6,'n=bar_index\na=n+1','a'))
        bad=('var n=close\na=n+1', 'n=close\na=n+1\nn:=open',
             'n=strategy.equity\na=n+1', 'n=request.security("TEST:EXPRESSIONS","2",close)\na=n+1',
             'n=barstate.islast ? 1 : 0\na=n+1','n="text"\na=n',
             'f(x) =>\n    request.security("TEST:EXPRESSIONS","2",x)\na=f(close)')
        for prefix in bad:
            with self.subTest(prefix=prefix),self.assertRaises(OpenPineError):self.session.compile(source(6,prefix,'a'))

    def test_forged_dependency_cycle_is_rejected(self):
        p=self.session.compile(source(6,'n=close','n'))
        declaration=next(n for n in p.statements if getattr(getattr(n,'binding',None),'name',None)=='n')
        forged=replace(declaration,expression=LoadIR(declaration.binding.binding_id,declaration.binding.value_type,declaration.expression.span))
        corrupted=replace(p,statements=tuple(forged if n is declaration else n for n in p.statements))
        with self.assertRaisesRegex(OpenPineError,'cycle'):request_nodes(corrupted)

    def test_unused_visible_globals_do_not_enter_function_dependency_closure(self):
        prefix='var unused=close\nother=bar_index\nf(x) => x+1'
        # The chart-only declaration is visible to the function contract,
        # but neither the function body nor its private program reads it.
        p=self.session.compile(source(6,prefix,'f(close)'))
        runtime=self.session.validate_program(p);node=request_nodes(p)[0]
        private=expression_program(p,node.arguments[2].expression,runtime,node.span)
        self.assertFalse(any(b.name in ('unused','other') for b in private.bindings))
        runtime.prepare(private,output_sink=legacy.Sink())

    def test_limits_cover_transitive_depth_and_total_closure_size(self):
        deep='n0=close\n'+'\n'.join(f'n{i}=n{i-1}+1' for i in range(1,20))
        with self.assertRaisesRegex(OpenPineError,'size/depth'):self.session.compile(source(6,deep,'n19'))
        wide='\n'.join(f'n{i}=close+{i}' for i in range(40))
        def balanced(names):
            if len(names)==1:return names[0]
            half=len(names)//2;return '('+balanced(names[:half])+'+'+balanced(names[half:])+')'
        with self.assertRaisesRegex(OpenPineError,'size/depth'):self.session.compile(source(6,wide,balanced([f'n{i}' for i in range(40)])))

    def test_bad_last_value_or_private_history_poison_restore(self):
        p=self.session.compile(source(6,'f(x) =>\n    x[1]','f(close)'))
        d=self.driver(p);saved=[];d.run(pause_after=5,checkpoint=lambda x:saved.append(loads(canonical(x.checkpoint()))))
        for field in ('last','history'):
            state=copy.deepcopy(saved[0]);record=next(iter(next(iter(state['groups'].values()))['records'].values()))
            if field=='last':record['last']=True
            else:next(iter(record['runtime']['history'].values()))['count']+=1
            restored=self.driver(p)
            with self.assertRaises((ValueError,OpenPineError)):restored.restore(state)
            self.assertTrue(restored.evaluation.closed)

    def test_private_persistent_and_kernel_state_cannot_be_injected_into_primary(self):
        prefix='f(x) =>\n    var float n=0\n    n:=n+x\n    ta.sma(n,2)'
        p=self.session.compile(source(6,prefix,'f(close)'))
        driver=self.driver(p);saved=[];driver.run(pause_after=5,checkpoint=lambda x:saved.append(loads(canonical(x.checkpoint()))))
        for field in ('persistent','states'):
            state=copy.deepcopy(saved[0]);private=next(iter(next(iter(state['groups'].values()))['records'].values()))['runtime']
            state['primary'][field]=copy.deepcopy(private[field])
            if field=='states':
                for row in state['primary'][field]:row['key'][1]='primary'
            restored=self.driver(p)
            with self.assertRaises(ValueError):restored.restore(state)
            self.assertTrue(restored.evaluation.closed)

    def test_replay_previews_and_restart_keep_derived_context_state(self):
        for v in (1,6):
            prefix='f(x) =>\n    var float n=0\n    n:=n+x\n    n\nbase=close*scale'
            p=self.session.compile(source(v,prefix,'f(base)[1]'))
            sink=legacy.Sink();baseline=ReplayEngine(self.session,p,(legacy.PRIMARY,legacy.HIGHER),'primary',sink)
            rows=legacy.events()
            for row in rows:baseline.apply(row)
            for cut in (3,9,len(rows)-1):
                first=ReplayEngine(self.session,p,(legacy.PRIMARY,legacy.HIGHER),'primary',legacy.Sink())
                for row in rows[:cut]:first.apply(row)
                state=loads(canonical(first.checkpoint()));out=legacy.Sink()
                second=ReplayEngine(self.session,p,(legacy.PRIMARY,legacy.HIGHER),'primary',out);second.restore(state)
                for row in rows[cut:]:second.apply(row)
                self.assertEqual(second.checkpoint(),baseline.checkpoint())


if __name__=='__main__':unittest.main()
