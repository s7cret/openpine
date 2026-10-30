"""Versioned optional calls and switch through the shared historical/replay IR."""
import unittest
from dataclasses import replace
from unittest.mock import patch

from packages.compiler.semantic.specification import ROOT
from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError, UserCallIR
from packages.contracts.codec import encode, decode
from packages.contracts import ProgramIR
from packages.execution.session import Session
from packages.marketdata import read_bars
from tools.conformance import run_cases


class SemanticCoreTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = read_bars(ROOT / 'conformance/functions/fixed.json')

    def source(self, body, version=6):
        return f'//@version={version}\n' + ('study' if version < 5 else 'indicator') + '("Semantic core")\n' + body + '\n'

    def run_body(self, body, version=6):
        return self.session.run(self.source(body, version), 'semantic-core.pine', data=self.data)

    def reject(self, body, code, version=6):
        with self.assertRaises(OpenPineError) as raised:self.run_body(body, version)
        self.assertEqual((raised.exception.diagnostic.code, raised.exception.diagnostic.language_version), (code, version))

    def test_optional_typed_named_and_signed_defaults(self):
        for v in (5, 6):
            _, result = self.run_body('f(float x=2, int scale=-3, bool yes=true, string label="x") => yes ? x*scale : 0\nplot(f())\nplot(f(scale=2, x=close))', v)
            self.assertEqual([o.values for o in result.outputs], [(-6.,)*4, (20., 40., 60., 80.)])

    def test_explicit_arguments_once_in_source_order_before_defaults(self):
        body = '''var a=array.new_int(1,0)
next() =>
    array.set(a,0,array.get(a,0)+1)
    array.get(a,0)
f(x, y=5, z=7) => x*100+y*10+z
plot(f(z=next(), x=next()))
plot(array.get(a,0))'''
        _, result = self.run_body(body)
        self.assertEqual([o.values for o in result.outputs], [(251,453,655,857),(2,4,6,8)])

    def test_default_na_and_override_are_checked_at_definition(self):
        _, result = self.run_body('f(float x=na) => na(x) ? 9 : x\nplot(f())\nplot(f(2))')
        self.assertEqual([o.values for o in result.outputs], [(9.,)*4, (2.,)*4])
        self.reject('f(x=na) => x\nplot(1)', 'FUNCTION_DEFAULT_TYPE')
        self.reject('f(int x="bad") => x\nplot(f(1))', 'FUNCTION_ARGUMENT_TYPE')
        self.reject('f(x=close) => x\nplot(1)', 'FUNCTION_DEFAULT_EXPRESSION')
        self.reject('f(x=1+2) => x\nplot(f(8))', 'FUNCTION_DEFAULT_EXPRESSION')

    def test_required_named_and_default_errors(self):
        self.reject('f(x,y=2) => x+y\nplot(f())', 'MISSING_ARGUMENT')
        self.reject('f(x=1) => x\nplot(f(x=2,x=3))', 'DUPLICATE_ARGUMENT')
        self.reject('f(x=1) => x\nplot(f(bad=2))', 'UNKNOWN_ARGUMENT')
        self.reject('f(x=1,y=2) => x+y\nplot(f(x=2,3))', 'POSITIONAL_AFTER_NAMED')

    def test_new_syntax_is_explicitly_unavailable_in_v1_v4(self):
        for v in range(1,5):
            self.reject('f(x=1) => x\nplot(f())', 'FUNCTION_DEFAULT_VERSION', v)
            self.reject('x=switch close\n    10 => 1\nplot(x)', 'SWITCH_VERSION', v)

    def test_selector_once_and_first_match_only(self):
        body = '''var a=array.new_int(1,0)
next() =>
    array.set(a,0,array.get(a,0)+1)
    array.get(a,0)
x=switch next()
    1 => 10
    1 => next()
    2 => 20
    => 30
plot(x)
plot(array.get(a,0))'''
        for v in (5,6):
            _, result = self.run_body(body,v)
            self.assertEqual([o.values for o in result.outputs], [(10,20,30,30),(1,2,3,4)])

    def test_condition_switch_scopes_and_lazy_conditions(self):
        body = '''var a=array.new_int(1,0)
later() =>
    array.set(a,0,array.get(a,0)+1)
    true
x=switch
    close<=20 =>
        local=close+1
        local
    later() => 99
    => 0
plot(x)
plot(array.get(a,0))'''
        _, result = self.run_body(body)
        self.assertEqual([o.values for o in result.outputs], [(11.,21.,99.,99.),(0,0,1,2)])
        self.reject('x=switch\n    true =>\n        local=1\n        local\n    => 2\nplot(local)', 'UNDEFINED_VARIABLE')

    def test_switch_statements_functions_tuples_and_nested_blocks(self):
        body = '''f(x, scale=2) =>
    switch
        x>20 =>
            y=switch x
                30 => 3
                => 4
            [y*scale,true]
        => [1,false]
[value, flag]=f(close)
var count=0
switch
    flag => count:=count+value
    => count:=count+1
plot(count)'''
        _, result = self.run_body(body)
        self.assertEqual(result.outputs[0].values, (1,2,8,16))

    def test_switch_missing_match_and_promotion(self):
        for v in (5,6):
            _, result = self.run_body('x=switch close\n    10 => true\nplot(x ? 1 : 0)\ny=switch close\n    10 => 1\n    20 => 2.5\nplot(y)',v)
            self.assertEqual(result.outputs[1].values,(1.,2.5,None,None))
        self.reject('x=switch close\n    10 => "x"\nplot(1)', 'STRING_BRANCH_REQUIRED')
        self.reject('x=switch\n    true => 1\n    => 2\n    false => 3\nplot(x)', 'SWITCH_DEFAULT_LAST')

    def test_branch_var_and_function_instances_have_independent_state(self):
        body = '''f(x=1) =>
    switch
        close<=20 =>
            var total=0
            total:=total+x
            total
        =>
            var total=100
            total:=total+x
            total
plot(f())
plot(f(2))'''
        _, result=self.run_body(body)
        self.assertEqual([o.values for o in result.outputs],[(1,2,101,102),(2,4,102,104)])

    def test_codec_and_runtime_reject_effectful_forged_defaults(self):
        program, before = self.run_body('f(x=2) => x\nplot(f())')
        decoded = decode(encode(program), ProgramIR)
        self.assertEqual(self.session.execute(decoded,data=self.data),before)
        plot = program.statements[-1]
        user = plot.arguments[0].expression
        self.assertIsInstance(user, UserCallIR)
        forged = replace(user, arguments=(replace(user.arguments[0], expression=plot),))
        invalid = replace(program, statements=(*program.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=forged),*plot.arguments[1:]))))
        with self.assertRaises(OpenPineError) as raised:self.session.execute(invalid,data=self.data)
        self.assertEqual(raised.exception.diagnostic.code,'IR_FUNCTION_DEFAULT')
        for index in (0, 'bad'):
            forged=replace(user,arguments=(replace(user.arguments[0],source_index=index),))
            invalid=replace(program,statements=(*program.statements[:-1],replace(plot,arguments=(replace(plot.arguments[0],expression=forged),*plot.arguments[1:]))))
            with self.assertRaises(OpenPineError) as raised:self.session.execute(invalid,data=self.data)
            self.assertEqual(raised.exception.diagnostic.code,'IR_FUNCTION_DEFAULT')

    def test_independent_versioned_conformance(self):
        report=run_cases(cases_path='conformance/semantic-core/cases.json')
        self.assertEqual((report['executed_programs'],report['expected_rejections']),(12,10))

    def test_switch_bounds_types_and_private_name_do_not_leak(self):
        self.reject('x=switch close\n'+''.join(f'    {i} => {i}\n' for i in range(33))+'plot(x)', 'SWITCH_LIMIT')
        self.reject('x=switch\n    1 => 2\n    => 3\nplot(x)', 'CONDITION_TYPE')
        self.reject('x=switch close\n    10 => 1\n    => "x"\nplot(x)', 'EXPRESSION_TYPE')
        _, result=self.run_body('_openpine_switch=99\nx=switch close\n    10 => _openpine_switch\n    => 0\nplot(x)\nplot(_openpine_switch)')
        self.assertEqual([o.values for o in result.outputs],[(99,0,0,0),(99,)*4])
        nested=['x=switch 0']
        for level in range(20):
            pad='    '*(level+1)
            nested.extend(pad+str(i)+' => 1' for i in range(31))
            nested.append(pad+('31 => switch 0' if level<19 else '31 => 1'))
        self.reject('\n'.join([*nested,'plot(x)']),'FUNCTION_EXPANSION')
        functions=['f0(x) => x']
        for level in range(1,6):
            functions.append(f'f{level}(x) =>\n    switch x\n'+''.join(f'        {i} => 1\n' for i in range(31))+f'        => f{level-1}(x)')
        self.reject('\n'.join([*functions,'plot(f5(close))']),'FUNCTION_EXPANSION')

    def test_profile_drift_rejects_new_syntax(self):
        from tools.semantic_core_spec import profile
        value=profile();value['versions']['6']['policy']['max_cases']=100
        with patch('packages.contracts.semantic_core.read_json',return_value=value):
            self.reject('f(x=2) => x\nplot(f())','SEMANTIC_CORE_PROFILE')


if __name__ == '__main__':unittest.main()
