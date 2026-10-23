"""Behavior of all five statement features across the six version profiles."""

import copy
import json
import subprocess
import sys
import unittest
from dataclasses import replace

from packages.compiler.semantic.specification import ROOT, SpecError
from packages.contracts import HISTORICAL_PROFILE_ID, LoadIR, OpenPineError, ValueType
from packages.execution.session import Session
from packages.marketdata import from_payload
from packages.runtime.engine import Runtime
from tools.conformance import LANGUAGE_CASES, run_cases


def source(version, body):
    declaration = "study" if version < 5 else "indicator"
    return f'//@version={version}\n{declaration}("Statements")\n{body}\n'


def data(values=(10, 20, 30, 40)):
    return from_payload({"schema_version": "openpine.historical-bars.1", "context_id": "statement.test",
        "symbol": "TEST:STATEMENTS", "timeframe": "1",
        "bars": [{"time": index * 60000, "close": value} for index, value in enumerate(values)]})


class StatementTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if sys.version_info[:3] != (3, 14, 8):
            raise RuntimeError("Tests require Python 3.14.8")
        cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)
        cls.data = data()

    def execute(self, version, body, values=None):
        return self.session.run(source(version, body), "statements.pine", data=self.data if values is None else data(values))

    def reject(self, version, body, code):
        with self.assertRaises(OpenPineError) as caught:
            self.execute(version, body)
        diagnostic = caught.exception.diagnostic
        self.assertEqual((diagnostic.code, diagnostic.language_version), (code, version))
        return diagnostic

    def runtime(self, program):
        catalog = self.session.catalog_for(program.identity.language_version)
        return Runtime(program.identity, catalog.contracts.values(), catalog.references.values())

    def test_all_five_features_run_in_all_versions(self):
        for version in range(1, 7):
            ema = "ema" if version < 5 else "ta.ema"
            body = f'''length = 3
src = close
average = {ema}(src, length)
difference = src - average
var float total = 0
ordinary = 1
ordinary := ordinary + 1
if src > 20
    total := total + src
else
    total := total - src
plot(difference)
plot(total)
plot(ordinary)'''
            with self.subTest(version=version):
                program, result = self.execute(version, body)
                self.assertEqual(result.outputs[0].values, (None, None, 10.0, 10.0) if version < 5 else (0.0, 5.0, 7.5, 8.75))
                self.assertEqual(result.outputs[1].values, (-10.0, -30.0, 0.0, 40.0))
                self.assertEqual(result.outputs[2].values, (2, 2, 2, 2))
                length = next(binding for binding in program.bindings if binding.name == 'length')
                self.assertEqual(length.value_type, ValueType('int', 'const'))
                self.assertEqual(program.schema_version, 'openpine.typed-ir.5')

    def test_var_and_ordinary_initialization_and_run_isolation(self):
        body = '''var counter = 0
counter := counter + 1
ordinary = 0
ordinary := ordinary + 1
plot(counter)
plot(ordinary)'''
        for version in range(1, 7):
            _, first = self.execute(version, body)
            _, second = self.execute(version, body)
            self.assertEqual(first, second)
            self.assertEqual(first.outputs[0].values, (1, 2, 3, 4))
            self.assertEqual(first.outputs[1].values, (1, 1, 1, 1))

    def test_block_var_initializes_on_first_reached_execution(self):
        body = '''var float seen = 0
if close > 20
    var float start = close
    start := start + 1
    seen := start
plot(seen)'''
        for version in range(1, 7):
            _, result = self.execute(version, body)
            self.assertEqual(result.outputs[0].values, (0.0, 0.0, 31.0, 32.0))

    def test_var_initializer_does_not_repeat_stateful_calls(self):
        for version in range(1, 7):
            ema = 'ema' if version < 5 else 'ta.ema'
            _, result = self.execute(version, f'''var float seen = 0
if close > 20
    var float average = {ema}(close, 1)
    seen := average
plot(seen)''')
            self.assertEqual(result.outputs[0].values, (0.0, 0.0, 30.0, 30.0))

    def test_shadowing_and_nearest_assignment_owner(self):
        body = '''x = close
var float accumulated = 0
if true
    x = 2.0
    if true
        x := x + 1.0
    accumulated := accumulated + x
plot(x)
plot(accumulated)'''
        for version in range(1, 7):
            _, result = self.execute(version, body)
            self.assertEqual(result.outputs[0].values, (10.0, 20.0, 30.0, 40.0))
            self.assertEqual(result.outputs[1].values, (3.0, 6.0, 9.0, 12.0))
            _, result = self.execute(version, 'x = close\nvar float seen = 0\nif true\n    x = x + 1.0\n    seen := x\nplot(seen)')
            self.assertEqual(result.outputs[0].values, (11.0, 21.0, 31.0, 41.0))

    def test_bad_lexical_references_are_rejected(self):
        examples = [
            ('a = b\nb = 1\nplot(a)', 'UNDEFINED_VARIABLE'),
            ('a := 1\nplot(1)', 'ASSIGN_UNDEFINED'),
            ('a = 1\na = 2\nplot(a)', 'DUPLICATE_VARIABLE'),
            ('if false\n    local = 1\nplot(local)', 'UNDEFINED_VARIABLE'),
            ('x = 1\nplot(x())', 'CALL_VARIABLE'),
        ]
        for version in range(1, 7):
            for body, code in examples:
                with self.subTest(version=version, body=body):
                    self.reject(version, body, code)

    def test_type_preservation_na_annotation_and_promotions(self):
        for version in range(1, 7):
            self.reject(version, 'int x = 1\nx := 1.5\nplot(x)', 'ASSIGNMENT_TYPE')
            self.reject(version, 'var x = 1\nx := true\nplot(x)', 'ASSIGNMENT_TYPE')
            self.reject(version, 'x = na\nplot(x)', 'UNTYPED_NA')
            _, result = self.execute(version, 'float x = 1\nx := close\nfloat missing = na\nplot(x)\nplot(missing + 1.0)')
            self.assertEqual(result.outputs[0].values, (10.0, 20.0, 30.0, 40.0))
            self.assertEqual(result.outputs[1].values, (None, None, None, None))

    def test_later_mutation_strengthens_earlier_reads_before_execution(self):
        from unittest.mock import patch
        with patch('packages.runtime.engine.Runtime.prepare', side_effect=AssertionError('runtime reached')):
            for version in range(1, 7):
                ema = 'ema' if version < 5 else 'ta.ema'
                self.reject(version, f'length = 3\nplot({ema}(close, length))\nlength := 4', 'ARGUMENT_QUALIFIER')

    def test_precedence_associativity_and_conditional_branch_types(self):
        body = '''a = 2 + 3 * 4
b = (2 + 3) * 4
c = 10 - 3 - 2
d = close > 20 ? 2 : close > 10 ? 1 : 0
e = close > 20 ? 2 : 1.5
plot(a)
plot(b)
plot(c)
plot(d)
plot(e)'''
        for version in range(1, 7):
            _, result = self.execute(version, body)
            self.assertEqual([output.values for output in result.outputs],
                             [(14,)*4, (20,)*4, (5,)*4, (0,1,2,2), (1.5,1.5,2.0,2.0)])

    def test_integer_division_zero_and_numeric_conditions_follow_policy(self):
        for version in range(1, 7):
            _, result = self.execute(version, 'plot(-5 / 2)\nplot(5.0 / 2)\nplot(1 / 0)')
            self.assertEqual(result.outputs[0].values, (-2,)*4 if version < 6 else (-2.5,)*4)
            self.assertEqual(result.outputs[1].values, (2.5,)*4)
            self.assertEqual(result.outputs[2].values, (None,)*4)
            if version < 6:
                _, result = self.execute(version, 'x = close ? 1 : 0\nplot(x)', (0,2,None,-1))
                self.assertEqual(result.outputs[0].values, (0,1,0,1))
            else:
                self.reject(version, 'x = close ? 1 : 0\nplot(x)', 'CONDITION_TYPE')

    def test_ternary_and_if_skip_unreached_calls(self):
        for version in range(1, 7):
            ema = 'ema' if version < 5 else 'ta.ema'
            _, result = self.execute(version, f'value = close > 20 ? {ema}(close,3) : 0.0\nplot(value)')
            self.assertEqual(result.outputs[0].values, (0.0,0.0,None,None) if version < 5 else (0.0,0.0,30.0,35.0))
            _, result = self.execute(version, f'float value = 0\nif close > 20\n    value := {ema}(close,3)\nplot(value)')
            self.assertEqual(result.outputs[0].values, (0.0,0.0,None,None) if version < 5 else (0.0,0.0,30.0,35.0))

    def test_and_or_call_state_is_strict_before_v6_and_lazy_in_v6(self):
        for version in range(1, 7):
            ema = 'ema' if version < 5 else 'ta.ema'
            _, result = self.execute(version, f'''a = close > 20 and {ema}(close,3) > 25
b = close < 30 or {ema}(close,3) > 25
plot(a ? 1 : 0)
plot(b ? 1 : 0)''')
            self.assertEqual(result.outputs[0].values, (0,0,0,1) if version < 6 else (0,0,1,1))
            self.assertEqual(result.outputs[1].values, (1,1,0,1) if version < 6 else (1,1,1,1))

    def test_reused_variable_evaluates_its_initializer_once_per_bar(self):
        for version in range(1, 7):
            ema = 'ema' if version < 5 else 'ta.ema'
            program, result = self.execute(version, f'average = {ema}(close,3)\nplot(average)\nplot(average)')
            self.assertEqual(result.outputs[0].values, result.outputs[1].values)
            self.assertEqual(result.outputs[0].values, (None,None,20.0,30.0) if version < 5 else (10.0,15.0,22.5,31.25))
            self.assertIsInstance(program.statements[-1].arguments[0].expression, LoadIR)

    def test_if_else_if_comments_and_multiline_calls(self):
        for version in range(1, 7):
            _, result = self.execute(version, '''var int state = 0
if close < 20
    // blank lines and comments do not create scopes

    state := 1
else if close <= 30
    state := 2
else
    state := 3
plot(
    state,
    "state")''')
            self.assertEqual(result.outputs[0].values, (1,2,2,3))

    def test_string_bool_values_and_comparisons_are_typed(self):
        for version in range(1, 7):
            _, result = self.execute(version, '''label = "va" + "lue"
flag = not (close < 20) and (close <= 30 or false)
same = close == 20
different = close != 20
plot(flag ? 1 : 0, label)
plot(same ? 1 : 0)
plot(different ? 1 : 0)''')
            self.assertEqual(result.outputs[0].title, 'value')
            self.assertEqual([output.values for output in result.outputs], [(0,1,1,0),(0,1,0,0),(1,0,1,1)])

    def test_global_only_calls_and_uncovered_constructs(self):
        for version in range(1, 7):
            self.reject(version, 'if true\n    plot(close)\nplot(close)', 'GLOBAL_ONLY_CALL')
            self.reject(version, 'varip float x = 0\nplot(x)', 'CONSTRUCT_UNCOVERED')
            self.reject(version, 'for x = 1\n    plot(x)', 'CONSTRUCT_UNCOVERED')

    def test_limits_and_malformed_blocks_are_diagnosed(self):
        for version in range(1, 7):
            # Tuples are parsed in every profile and require at least two items.
            self.reject(version, 'plot([close])', 'TUPLE_ARITY')
        self.reject(6, 'if true\nplot(close)', 'BLOCK_REQUIRED')
        self.reject(6, 'if true\n  x = 1\nplot(close)', 'INDENTATION')
        self.reject(6, 'if true\n\tx = 1\nplot(close)', 'INDENTATION')
        self.reject(6, 'x = ' + '+'.join(['1']*80) + '\nplot(x)', 'EXPRESSION_DEPTH')

    def test_ir_rejects_scope_and_policy_forgery(self):
        program = self.session.compile(source(6, 'x = close\nif true\n    x = 1.0\n    x := 2.0\nplot(x)'))
        output = program.statements[-1]
        local = next(binding for binding in program.bindings if binding.scope_id != 'scope:root')
        fake_load = LoadIR(local.binding_id, local.value_type, output.span)
        fake_output = replace(output, arguments=(replace(output.arguments[0], expression=fake_load), output.arguments[1]))
        forged = replace(program, statements=(*program.statements[:-1], fake_output))
        with self.assertRaises(OpenPineError) as caught:
            self.runtime(program).admit(forged)
        self.assertEqual(caught.exception.diagnostic.code, 'IR_VARIABLE_SCOPE')
        forged = replace(program, language_policy=replace(program.language_policy, integer_division='truncate'))
        with self.assertRaises(OpenPineError) as caught:
            self.runtime(program).admit(forged)
        self.assertEqual(caught.exception.diagnostic.code, 'IR_LANGUAGE_POLICY')
        conditional = program.statements[2]
        root = next(binding for binding in program.bindings if binding.scope_id == 'scope:root')
        fake_assignment = replace(conditional.body[1], binding=root)
        forged = replace(program, statements=(*program.statements[:2], replace(conditional, body=(conditional.body[0], fake_assignment)), output))
        with self.assertRaises(OpenPineError) as caught:
            self.runtime(program).admit(forged)
        self.assertEqual(caught.exception.diagnostic.code, 'IR_VARIABLE_SCOPE')

    def test_changed_valid_policy_is_rejected_by_runtime_capabilities(self):
        snapshot = self.session.snapshot
        language = copy.deepcopy(snapshot.language)
        language['versions']['6']['integer_division'] = 'truncate'
        with self.assertRaises(SpecError) as caught:
            Session(snapshot=replace(snapshot, language=language), profile_id=HISTORICAL_PROFILE_ID)
        self.assertEqual(caught.exception.code, 'IMPLEMENTATION_MISSING')

    def test_independent_statement_corpus_covers_every_version(self):
        report = run_cases(self.session, cases_path=LANGUAGE_CASES)
        self.assertEqual((report['executed_programs'], report['expected_rejections']), (47,37))
        self.assertEqual(set(report['operations_by_version']), {'1','2','3','4','5','6'})
        for values in report['operations_by_version'].values():
            self.assertTrue({'variable.declare','variable.assign','variable.var','statement.if','operator.conditional'} <= set(values))

    def test_cli_runs_an_actual_statement_program(self):
        bootstrap = "import runpy,sys;sys.path.insert(0,sys.argv.pop(1));runpy.run_module('apps.openpine',run_name='__main__')"
        for version in (1,6):
            completed = subprocess.run([sys.executable,'-X','utf8','-c',bootstrap,str(ROOT),'run','--source',
                f'conformance/language/v{version}-five-points.pine','--bars','conformance/language/fixed.json'],
                cwd=ROOT,capture_output=True,text=True,encoding='utf8',check=False)
            self.assertEqual(completed.returncode,0,completed.stderr + completed.stdout)
            result=json.loads(completed.stdout)
            self.assertEqual(result['identity']['language_version'],version)
            self.assertEqual(result['result']['outputs'][1]['values'],[-10.0,-30.0,0.0,40.0])


if __name__ == '__main__':
    unittest.main()
