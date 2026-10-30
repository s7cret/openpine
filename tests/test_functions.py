"""Scalar function and value-block behavior, isolation and admission boundaries."""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

from packages.compiler.semantic.specification import ROOT, SpecError, encode, load_snapshot, read_json, sha
from packages.contracts import BlockIR, HISTORICAL_PROFILE_ID, LiteralIR, OpenPineError, StoreIR, UserCallIR, ValueType
from packages.execution.session import Session
from packages.marketdata import from_payload, read_bars
from packages.runtime.engine import Runtime
from packages.runtime.operations import KERNELS
from tools.conformance import FUNCTION_CASES, run_cases
from tools.function_spec import prepare


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Functions")\n{body}\n'


def bars(values):
    return from_payload({'schema_version': 'openpine.historical-bars.1', 'context_id': 'functions.unit',
        'symbol': 'TEST:FUNCTIONS', 'timeframe': '1',
        'bars': [{'time': i * 60000, 'close': value} for i, value in enumerate(values)]})


class FunctionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = bars([10, 20, 30, 40])

    def execute(self, version, body, values=None):
        return self.session.run(source(version, body), 'functions.pine', data=self.data if values is None else bars(values))

    def reject(self, version, body, code):
        with self.assertRaises(OpenPineError) as caught:
            self.execute(version, body)
        self.assertEqual((caught.exception.diagnostic.code, caught.exception.diagnostic.language_version), (code, version))

    def runtime(self, program):
        catalog = self.session.catalog_for(program.identity.language_version)
        return Runtime(program.identity, catalog.contracts.values(), catalog.references.values())

    def test_independent_corpus_covers_all_five_points_in_every_version(self):
        report = run_cases(self.session, cases_path=FUNCTION_CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (60, 48))
        required = {'function.call', 'function.parameter', 'block.value', 'series.history', 'variable.var', 'series.sma'}
        for version in range(1, 7):
            self.assertTrue(required <= set(report['operations_by_version'][str(version)]))
            self.assertEqual(sum(row['language_version'] == version for row in report['results']), 18)

    def test_inferred_parameters_specialize_int_float_bool_and_string(self):
        body = '''twice(x) => x + x
flip(x) => not x
decorate(x) => x + "!"
plot(twice(3))
plot(twice(2.5))
plot(flip(true) ? 1 : 0)
plot(1, decorate("ok"))'''
        for version in range(1, 7):
            program, result = self.execute(version, body)
            self.assertEqual([o.values for o in result.outputs], [(6,)*4, (5.0,)*4, (0,)*4, (1,)*4])
            self.assertEqual(result.outputs[-1].title, 'ok!')
            self.assertEqual(program.schema_version, 'openpine.typed-ir.5')
            self.assertEqual(len(program.functions), 3)

    def test_typed_parameters_promote_int_to_float_and_preserve_series(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'scale(float value, int factor) => value * factor\nplot(scale(3, 2))\nplot(scale(close, 2))')
            self.assertEqual([o.value_type for o in result.outputs], [ValueType('float'), ValueType('float', 'series')])
            self.assertEqual([o.values for o in result.outputs], [(6.0,)*4, (20.0,40.0,60.0,80.0)])
            self.reject(version, 'f(int x) => x\nplot(f(1.5))', 'FUNCTION_ARGUMENT_TYPE')
            self.reject(version, 'f(x) => x\nplot(f(na))', 'FUNCTION_ARGUMENT_TYPE')

    def test_explicit_parameter_qualifiers_and_parameter_history(self):
        for version in range(1, 7):
            ema = 'ema' if version < 5 else 'ta.ema'
            _, result = self.execute(version, f'f(float x, simple int length) => {ema}(x, length)\nplot(f(close, 1))')
            self.assertEqual(result.outputs[0].values, (10.0,20.0,30.0,40.0))
            self.reject(version, 'f(simple int x) => x\nplot(f(bar_index))', 'FUNCTION_ARGUMENT_QUALIFIER')
            self.reject(version, 'f(simple int x) => nz(x[1])\nplot(f(3))', 'FUNCTION_ARGUMENT_QUALIFIER')
            _, result = self.execute(version, 'f(series int x) => x\nplot(f(3))')
            self.assertEqual(result.outputs[0].value_type, ValueType('int', 'series'))

    def test_named_arguments_evaluate_once_in_source_order(self):
        for version in range(1, 7):
            observed = []
            kernels = dict(KERNELS)
            original = kernels['numeric.abs']

            def absolute(arguments, context):
                observed.append(arguments[0])
                return original.function(arguments, context)

            kernels['numeric.abs'] = replace(original, function=absolute)
            absolute_name = 'abs' if version < 5 else 'math.abs'
            body = f'pair(a, b) => a * 10 + b\nplot(pair(b={absolute_name}(-3), a={absolute_name}(-2)))'
            with patch('packages.runtime.engine.KERNELS', kernels):
                _, result = self.execute(version, body, [10])
            self.assertEqual(observed, [-3, -2])
            self.assertEqual(result.outputs[0].values, (23,))

    def test_function_argument_binding_errors_are_static(self):
        for version in range(1, 7):
            for call, code in [('pair(1)', 'MISSING_ARGUMENT'), ('pair(1,2,3)', 'TOO_MANY_ARGUMENTS'),
                               ('pair(1,a=2)', 'DUPLICATE_ARGUMENT'), ('pair(z=1,b=2)', 'UNKNOWN_ARGUMENT'),
                               ('pair(a=1,2)', 'POSITIONAL_AFTER_NAMED')]:
                self.reject(version, f'pair(a,b) => a+b\nplot({call})', code)

    def test_multiline_last_expression_and_if_values(self):
        body = '''adjust(x) =>
    doubled = x * 2
    if x > 20
        local = doubled + 1
        local
    else if x == 20
        2.5
    else
        1
plot(adjust(close))'''
        for version in range(1, 7):
            _, result = self.execute(version, body)
            self.assertEqual(result.outputs[0].values, (1.0,2.5,61.0,81.0))

    def test_value_if_is_lazy_and_checks_both_branch_types(self):
        for version in range(1, 7):
            sma = 'sma' if version < 5 else 'ta.sma'
            _, result = self.execute(version, f'''value = if close > 20
    {sma}(close, 2)
else
    0
plot(value)''')
            self.assertEqual(result.outputs[0].values, (0.0,0.0,None,35.0))
            self.reject(version, 'f(x) =>\n    if true\n        1\n    else\n        "bad"\nplot(f(close))', 'EXPRESSION_TYPE')
            self.reject(version, 'f(x) =>\n    if true\n        1\n    else\n        unknown\nplot(f(close))', 'UNDEFINED_VARIABLE')

    def test_missing_else_uses_numeric_na_and_version_specific_bool(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'value = if close > 20\n    1\nplot(value)')
            self.assertEqual(result.outputs[0].values, (None,None,1,1))
            body = 'flag = if close > 20\n    true\n'
            if version < 6:
                body += 'plot(na(flag) ? 1 : 0)'
                expected = (1,1,0,0)
            else:
                body += 'plot(flag ? 1 : 0)'
                expected = (0,0,1,1)
            _, result = self.execute(version, body)
            self.assertEqual(result.outputs[0].values, expected)
            self.reject(version, 'text = if true\n    "value"\nplot(1,text)', 'STRING_BRANCH_REQUIRED')

    def test_value_block_locals_do_not_escape_or_leak_between_branches(self):
        for version in range(1, 7):
            self.reject(version, 'x = if true\n    hidden = 1\n    hidden\nelse\n    hidden\nplot(x)', 'UNDEFINED_VARIABLE')
            self.reject(version, 'x = if true\n    hidden = 1\n    hidden\nelse\n    0\nplot(hidden)', 'UNDEFINED_VARIABLE')
            _, result = self.execute(version, 'x = if close > 20\n    local = 1\n    local\nelse\n    local = 2\n    local\nplot(x)')
            self.assertEqual(result.outputs[0].values, (2,2,1,1))

    def test_globals_are_read_at_call_time_and_caller_locals_are_not_captured(self):
        body = '''offset = 1
read_offset(x) => x + offset
offset := offset + 1
var float seen = 0
if true
    offset = 100
    seen := read_offset(close)
plot(seen)'''
        for version in range(1, 7):
            _, result = self.execute(version, body)
            self.assertEqual(result.outputs[0].values, (12.0,22.0,32.0,42.0))
            self.reject(version, 'f() => future\nfuture = 1\nplot(f())', 'UNDEFINED_VARIABLE')
            self.reject(version, 'plot(f())\nfuture = 1\nf() => future', 'FUNCTION_CAPTURE_ORDER')

    def test_globals_and_parameters_cannot_be_reassigned_even_in_unused_functions(self):
        for version in range(1, 7):
            for body in ['value = 1\nf(x) =>\n    value := x\n    x\nplot(1)',
                         'f(x) =>\n    x := 1\n    x\nplot(1)']:
                self.reject(version, body, 'FUNCTION_WRITE')

    def test_local_shadowing_and_reassignment_use_the_nearest_owner(self):
        body = '''x = 100
f(x) =>
    var float seen = 0
    if true
        x = x + 1
        x := x * 2
        seen := x
    seen
plot(f(close))
plot(x)'''
        for version in range(1, 7):
            _, result = self.execute(version, body)
            self.assertEqual([o.values for o in result.outputs], [(22.0,42.0,62.0,82.0),(100,)*4])

    def test_two_calls_have_independent_var_and_history_cells(self):
        body = '''acc(x) =>
    var float total = 0
    total := nz(total[1]) + x
    total
plot(acc(close))
plot(acc(1))'''
        for version in range(1, 7):
            program, result = self.execute(version, body)
            self.assertEqual([o.values for o in result.outputs], [(10.0,30.0,60.0,100.0),(1.0,2.0,3.0,4.0)])
            ids = [binding.binding_id for binding in program.bindings]
            self.assertEqual(len(ids), len(set(ids)))
            self.assertEqual(len({binding.namespace for binding in program.bindings}), 2)

    def test_nested_written_call_chains_have_independent_state(self):
        for version in range(1, 7):
            sma = 'sma' if version < 5 else 'ta.sma'
            body = f'inner(x) => {sma}(x,2)\nouter(x) => inner(x)+inner(x*2)\nplot(outer(close))\nplot(outer(1))'
            _, result = self.execute(version, body)
            self.assertEqual([o.values for o in result.outputs], [(None,45.0,75.0,105.0),(None,3.0,3.0,3.0)])

    def test_ema_in_skipped_function_calls_keeps_each_versions_seed_policy(self):
        for version in range(1, 7):
            ema = 'ema' if version < 5 else 'ta.ema'
            body = f'average(x) => {ema}(x,3)\nvar float seen = 0\nif bar_index % 2 == 0\n    seen := average(close)\nplot(seen)'
            _, result = self.execute(version, body, [10,20,30,40,50,60])
            self.assertEqual(result.outputs[0].values, (None,None,None,None,30.0,30.0) if version < 5 else (10.0,10.0,20.0,20.0,35.0,35.0))

    def test_parameter_and_expression_history_is_bar_aligned_with_holes(self):
        body = '''previous(x) => nz(x[1])
expression() => nz((close * 2)[1])
var float first = 0
var float second = 0
if bar_index % 2 == 0
    first := previous(close)
    second := expression()
plot(first)
plot(second)
plot(previous(close))
plot(expression())'''
        for version in range(1, 7):
            _, result = self.execute(version, body)
            self.assertEqual([o.values for o in result.outputs], [(0.0,)*4,(0.0,)*4,(0.0,10.0,20.0,30.0),(0.0,20.0,40.0,60.0)])

    def test_skipped_var_initializer_inside_a_function_does_not_repeat(self):
        for version in range(1, 7):
            sma = 'sma' if version < 5 else 'ta.sma'
            body = f'first(x) =>\n    var float initial = {sma}(x,1)\n    initial\nplot(first(close))\nplot(first(close*2))'
            _, result = self.execute(version, body)
            self.assertEqual([o.values for o in result.outputs], [(10.0,)*4,(20.0,)*4])

    def test_new_runs_and_extended_data_do_not_share_function_state(self):
        body = 'acc(x) =>\n    var float total = 0\n    total := total + x\n    total\nplot(acc(close))'
        for version in range(1, 7):
            _, first = self.execute(version, body)
            _, again = self.execute(version, body)
            _, longer = self.execute(version, body, [10,20,30,40,1000])
            self.assertEqual(first, again)
            self.assertEqual(first.outputs[0].values, longer.outputs[0].values[:4])

    def test_forward_calls_zero_arguments_and_definitions_before_declaration(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'outer() => inner()\ninner() => 3\nplot(outer())')
            self.assertEqual(result.outputs[0].values, (3,)*4)
            declaration = 'study' if version < 5 else 'indicator'
            script = f'//@version={version}\nf(x) => x+1\n{declaration}("before")\nplot(f(1))'
            _, result = self.session.run(script, data=self.data)
            self.assertEqual(result.outputs[0].values, (2,)*4)

    def test_recursion_duplicate_names_and_nested_definitions_are_diagnosed(self):
        for version in range(1, 7):
            for body, code in [('f(x) => f(x)\nplot(1)', 'FUNCTION_RECURSION'),
                               ('f(x) => g(x)\ng(x) => f(x)\nplot(1)', 'FUNCTION_RECURSION'),
                               ('f(x) => x\nf(y) => y\nplot(1)', 'DUPLICATE_FUNCTION' if version<5 else 'FUNCTION_OVERLOAD_TYPE'),
                               ('f(x,x) => x\nplot(1)', 'DUPLICATE_PARAMETER'),
                               ('f(x) =>\n    inner(y) => y\n    x\nplot(1)', 'FUNCTION_GLOBAL_ONLY')]:
                self.reject(version, body, code)

    def test_global_only_effects_and_function_return_errors_are_static(self):
        for version in range(1, 7):
            for body, code in [('f(x) => plot(x)\nplot(1)', 'GLOBAL_ONLY_CALL'),
                               ('f(x) =>\n    local = x\nplot(1)', 'FUNCTION_RETURN'),
                               ('f() => na\nplot(f())', 'FUNCTION_RETURN')]:
                self.reject(version, body, code)
            if version < 5:
                self.reject(version, 'f(x=1) => x\nplot(1)', 'FUNCTION_DEFAULT_VERSION')
            else:
                _, result = self.execute(version, 'f(x=1) => x\nplot(f())')
                self.assertEqual(result.outputs[0].values, (1, 1, 1, 1))

    def test_result_qualifier_includes_unused_stateful_body_dependencies(self):
        for version in range(1, 7):
            sma = 'sma' if version < 5 else 'ta.sma'
            declaration = 'study' if version < 5 else 'indicator'
            script = f'//@version={version}\ntitle() =>\n    unused = {sma}(close,2)\n    "name"\n{declaration}(title())\nplot(close)'
            with self.assertRaises(OpenPineError) as caught:
                self.session.run(script, data=self.data)
            self.assertEqual(caught.exception.diagnostic.code, 'ARGUMENT_QUALIFIER')

    def test_compile_expansion_and_definition_limits_are_bounded(self):
        self.reject(6, '\n'.join(f'f{i}() => {i}' for i in range(65))+'\nplot(1)', 'FUNCTION_LIMIT')
        self.reject(6, 'f('+','.join(f'x{i}' for i in range(17))+') => 1\nplot(1)', 'FUNCTION_LIMIT')
        chain = '\n'.join(f'f{i}() => f{i+1}()' for i in range(12))+'\nf12() => 1\nplot(f0())'
        self.reject(6, chain, 'FUNCTION_LIMIT')
        tree = 'f0() => 1\n'+'\n'.join(f'f{i}() => f{i-1}()+f{i-1}()' for i in range(1,9))+'\nplot(f8())'
        self.reject(6, tree, 'FUNCTION_LIMIT')

    def test_pure_functions_work_in_the_literal_profile(self):
        session = Session()
        _, result = session.run('//@version=6\nf(x) => x*2\nindicator("literal")\nplot(f(3))')
        self.assertEqual(result.outputs[0].values, (6,))

    def test_ir_rejects_function_identity_capture_order_and_result_forgery(self):
        program, _ = self.execute(6, 'offset = 2\nf(x,y) => x+y+offset\nplot(f(close,1))')
        output = program.statements[-1]
        call = output.arguments[0].expression
        self.assertIsInstance(call, UserCallIR)
        for forged_call, code in [(replace(call,function_id='function:unknown'),'IR_USER_CALL'),
                                  (replace(call,call_site_id='user:other'),'IR_USER_CALL'),
                                  (replace(call,evaluation_order=(1,0)),'IR_EVALUATION_ORDER'),
                                  (replace(call,value_type=ValueType('int','series')),'IR_FUNCTION_RESULT')]:
            forged = replace(program, statements=(*program.statements[:-1], replace(output,
                arguments=(replace(output.arguments[0],expression=forged_call),*output.arguments[1:]))))
            with self.assertRaises(OpenPineError) as caught:
                self.runtime(program).prepare(forged)
            self.assertEqual(caught.exception.diagnostic.code, code)
        forged = replace(program, functions=(replace(program.functions[0],captures=()),))
        with self.assertRaises(OpenPineError) as caught:
            self.runtime(program).prepare(forged)
        self.assertEqual(caught.exception.diagnostic.code,'IR_VARIABLE_SCOPE')

    def test_ir_rejects_parameter_writes_and_shared_builtin_call_sites(self):
        program, _ = self.execute(6, 'f(x) => ta.sma(x,2)\nplot(f(close))')
        output = program.statements[-1]
        call = output.arguments[0].expression
        parameter = call.parameters[0]
        write = StoreIR(parameter,LiteralIR(1.0,ValueType('float'),call.span),False,call.span)
        for bad, code in [(replace(call,statements=(write,)),'IR_FUNCTION_WRITE'),
                          (replace(call,result=replace(call.result,call_site_id=f'call:{call.result.span.start}:{call.result.span.end}')),'IR_CALL_SITE')]:
            forged = replace(program,statements=(*program.statements[:-1],replace(output,
                arguments=(replace(output.arguments[0],expression=bad),*output.arguments[1:]))))
            with self.assertRaises(OpenPineError) as caught:
                self.runtime(program).prepare(forged)
            self.assertEqual(caught.exception.diagnostic.code,code)

    def test_ir_rejects_forged_value_block_scope(self):
        program, _ = self.execute(6, 'value = if true\n    1\nelse\n    2\nplot(value)')
        store = program.statements[1]
        conditional = store.expression
        self.assertIsInstance(conditional.when_true,BlockIR)
        bad = replace(conditional,when_true=replace(conditional.when_true,scope_id='scope:root'))
        forged = replace(program,statements=(program.statements[0],replace(store,expression=bad),*program.statements[2:]))
        with self.assertRaises(OpenPineError) as caught:
            self.runtime(program).prepare(forged)
        self.assertEqual(caught.exception.diagnostic.code,'IR_BLOCK_SCOPE')

    def changed_spec(self, change):
        parent=ROOT/'.runtime/function-tests'
        parent.mkdir(parents=True,exist_ok=True)
        temporary=tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temporary.cleanup)
        root=Path(temporary.name)
        shutil.copytree(ROOT/'spec',root/'spec')
        lock=read_json(root/'spec/lock.json')
        directory=root/lock['function_release']['path']
        data=read_json(directory/'functions.json')
        change(data)
        (directory/'functions.json').write_bytes(encode(data))
        manifest=read_json(directory/'manifest.json')
        manifest['files']['functions.json']=sha((directory/'functions.json').read_bytes())
        (directory/'manifest.json').write_bytes(encode(manifest))
        lock['function_release']['manifest_sha256']=sha((directory/'manifest.json').read_bytes())
        (root/'spec/lock.json').write_bytes(encode(lock))
        return root

    def test_repinned_foreign_version_evidence_and_wrong_policy_are_rejected(self):
        root=self.changed_spec(lambda data:data['versions']['6']['evidence'].__setitem__(0,copy.deepcopy(data['versions']['1']['evidence'][0])))
        with self.assertRaises(SpecError) as caught:
            load_snapshot(root)
        self.assertEqual(caught.exception.code,'SPEC_PROVENANCE')
        root=self.changed_spec(lambda data:data['versions']['6']['policy'].__setitem__('missing_bool_branch','na'))
        with self.assertRaises(SpecError) as caught:
            Session(load_snapshot(root),profile_id=HISTORICAL_PROFILE_ID)
        self.assertEqual(caught.exception.code,'IMPLEMENTATION_MISSING')

    def test_function_release_is_reproducible_and_existing_bundles_are_preserved(self):
        directory=ROOT/self.session.snapshot.lock['function_release']['path']
        for name, raw in prepare().items():
            self.assertEqual((directory/name).read_bytes(),raw)
        self.assertEqual(self.session.snapshot.lock['series_release'], {'manifest_sha256': '98be9070ef98dff218547ac328f90492212e37215a8651e1425e3117b7229d7c', 'path': 'spec/bundles/pine-series-20261021.1'})

    def test_cli_runs_combined_functions_for_v1_and_v6(self):
        for version in (1,6):
            process=subprocess.run([sys.executable,'-X','utf8','-m','apps.openpine','run','--source',
                f'conformance/functions/v{version}-combined.pine','--bars','conformance/functions/fixed.json'],
                cwd=ROOT,capture_output=True,text=True,encoding='utf-8')
            self.assertEqual(process.returncode,0,process.stderr+process.stdout)
            payload=json.loads(process.stdout)
            self.assertEqual(payload['result']['outputs'][0]['values'],[10.0,40.0,90.0,141.0])


if __name__ == '__main__':
    unittest.main()
