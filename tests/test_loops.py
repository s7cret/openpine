"""Independent loop arithmetic, version boundaries and hostile IR admission."""

import unittest
from dataclasses import replace
from unittest.mock import patch

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError, ValueType, ProgramIR, EvaluateIR
from packages.contracts.codec import decode, encode as encode_ir
from packages.contracts.loops import ITERATIONS_PER_BAR, OPERATIONS_PER_BAR
from packages.execution.session import Session
from packages.marketdata import from_payload


def source(version, body):
    return f'//@version={version}\n' + ('study' if version < 5 else 'indicator') + '("Loops")\n' + body + '\n'


def bars(values=(10.0, 20.0, 30.0, 40.0)):
    return from_payload({'schema_version': 'openpine.historical-bars.1', 'context_id': 'loops.unit',
        'symbol': 'TEST:LOOPS', 'timeframe': '1',
        'bars': [{'time': i*60000, 'close': v} for i, v in enumerate(values)]})


class LoopTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def run_body(self, version, body, values=(10.0, 20.0, 30.0, 40.0)):
        return self.session.run(source(version, body), 'loops.pine', data=bars(values))

    def reject(self, version, body, code):
        with self.assertRaises(OpenPineError) as caught:self.run_body(version, body)
        self.assertEqual(caught.exception.diagnostic.code, code)
        self.assertIsNotNone(caught.exception.diagnostic.span)
        self.assertEqual(caught.exception.diagnostic.language_version, version)

    def test_availability_in_all_six_versions_including_unused_functions(self):
        self.reject(1, 'for i = 0 to 2\n    x = i\nplot(1)', 'LOOP_VERSION')
        for v in range(1, 5):
            self.reject(v, 'while false\n    x = 1\nplot(1)', 'LOOP_VERSION')
            self.reject(v, 'unused() =>\n    while false\n        1\nplot(1)', 'LOOP_VERSION')
        for v in range(2, 7):
            p, r = self.run_body(v, 'sum = 0\nfor i = 0 to 3\n    sum := sum + i\nplot(sum)')
            self.assertEqual(p.schema_version, 'openpine.typed-ir.8')
            self.assertEqual(r.outputs[0].values, (6,)*4)

    def test_inclusive_bounds_descending_positive_step_equal_and_na(self):
        for v in range(2, 7):
            _, r = self.run_body(v, '''a = 0
for i = 5 to 0 by 2
    a := a * 10 + i
b = 0
for j = -2 to 3 by 2
    b := b + j
c = 0
for k = 3 to 3
    c := c + k
for skipped = 1 to na
    c := 99
plot(a)
plot(b)
plot(c)''')
            self.assertEqual([o.values for o in r.outputs], [(531,)*4, (0,)*4, (3,)*4])

    def test_v6_dynamic_end_and_legacy_frozen_end(self):
        body = '''end = 4
sum = 0
for i = 0 to end
    sum := sum + 1
    end := 1
plot(sum)'''
        for v in range(2, 7):
            _, r = self.run_body(v, body)
            self.assertEqual(r.outputs[0].values, ((2 if v == 6 else 5),)*4)

    def test_v6_direction_is_frozen_even_when_boundary_moves(self):
        _, r = self.run_body(6, '''end = 0
sum = 0
for i = 4 to end by 2
    sum := sum + i
    end := 2
plot(sum)''')
        self.assertEqual(r.outputs[0].values, (6,)*4)

    def test_initial_and_step_are_evaluated_once(self):
        for v in range(2, 7):
            _, r = self.run_body(v, '''step = 1
sum = 0
for i = 0 to 3 by step
    step := 9
    sum := sum + i
plot(sum)''')
            self.assertEqual(r.outputs[0].values, (6,)*4)

    def test_final_expression_evaluation_order_and_break(self):
        from packages.runtime.operations import KERNELS
        observed = []
        original = KERNELS['numeric.abs']
        def traced(arguments, context):
            observed.append(arguments[0]);return original.function(arguments, context)
        for v in (5,6):
            for stop in (False,True):
                observed.clear()
                body='for i = math.abs(0) to math.abs(2) by math.abs(1)\n    '+('break' if stop else 'x = i')+'\nplot(close)'
                with patch('packages.runtime.engine.KERNELS', {**KERNELS,'numeric.abs':replace(original,function=traced)}):self.run_body(v, body, (10.0,))
                self.assertEqual(observed, [0,2,1]+([2,2,2] if v==6 and not stop else []))

    def test_forged_control_cannot_cross_a_user_function_boundary(self):
        from packages.contracts import LoopControlIR
        p = self.session.compile(source(6, 'f() => 1\nfor i = 0 to 1\n    f()\nplot(1)'))
        loop = p.statements[1].expression
        call = loop.statements[0].expression
        forged = replace(call, statements=(LoopControlIR('break',loop.loop_id,call.span),))
        loop = replace(loop, statements=(EvaluateIR(forged,call.span),))
        with self.assertRaises(OpenPineError) as caught:self.session.validate_program(replace(p,statements=(p.statements[0],EvaluateIR(loop,loop.span),p.statements[-1])))
        self.assertEqual(caught.exception.diagnostic.code, 'IR_LOOP_CONTROL')

    def test_nested_loops_have_distinct_scopes_and_nearest_control(self):
        for v in range(2, 7):
            _, r = self.run_body(v, '''i = 99
sum = 0
for i = 1 to 3
    for j = 1 to 4
        if j == 2
            continue
        if j == 4
            break
        sum := sum + i * j
plot(sum)
plot(i)''')
            self.assertEqual([o.values for o in r.outputs], [(24,)*4, (99,)*4])
            self.reject(v, 'for i = 0 to 1\n    x = i\nplot(x)', 'UNDEFINED_VARIABLE')
            self.reject(v, 'for i = 0 to 1\n    i := 10\nplot(1)', 'LOOP_COUNTER_WRITE')

    def test_while_condition_is_checked_before_every_iteration(self):
        for v in (5, 6):
            _, r = self.run_body(v, '''i = 0
sum = 0
while i < 5
    i := i + 1
    if i == 2
        continue
    if i == 4
        break
    sum := sum + i
plot(sum)''')
            self.assertEqual(r.outputs[0].values, (4,)*4)
        self.run_body(5, 'n = 2\nwhile n\n    n := n - 1\nplot(n)')
        self.reject(6, 'while 1\n    break\nplot(1)', 'CONDITION_TYPE')

    def test_loop_value_is_last_completed_tail_before_break_or_continue(self):
        for v in range(2, 7):
            _, r = self.run_body(v, '''value = for i = 1 to 6
    if i == 2
        continue
    if i == 4
        break
    i * 10
plot(value)''')
            self.assertEqual(r.outputs[0].values, (30,)*4)
            self.assertEqual(r.outputs[0].value_type, ValueType('int', 'series'))

    def test_numeric_bool_and_tuple_missing_results(self):
        for v in range(2, 7):
            _, r = self.run_body(v, '''a = for i = na to 2
    i
b = for i = 1 to 2
    break
    7
[number, flag] = for i = na to 2
    [i, true]
plot(a)
plot(b)
plot(number)
plot(flag ? 1 : 0)''')
            self.assertEqual([o.values for o in r.outputs[:3]], [(None,)*4]*3)
            self.assertEqual(r.outputs[-1].values, (0,)*4)
        for v in (5, 6):
            _, r = self.run_body(v, 'value = while false\n    1.5\nplot(value)')
            self.assertEqual(r.outputs[0].values, (None,)*4)

    def test_assignment_and_if_tail_return_values_and_udf(self):
        for v in range(2, 7):
            _, r = self.run_body(v, '''sum_to(n) =>
    sum = 0
    for i = 1 to n
        sum := sum + i
last = for i = 0 to 2
    if i == 2
        2.5
    else
        1
plot(sum_to(4))
plot(last)''')
            self.assertEqual([o.values for o in r.outputs], [(10,)*4, (2.5,)*4])

    def test_history_offsets_follow_bars_and_use_last_loop_cell(self):
        for v in range(2, 7):
            _, r = self.run_body(v, '''sum = 0.0
for i = 0 to 2
    sum := sum + nz(close[i])
value = for j = 0 to 2
    x = j + bar_index
    nz(x[1])
plot(sum)
plot(value)''')
            self.assertEqual([o.values for o in r.outputs], [(10.0,30.0,60.0,90.0), (0,2,3,4)])

    def test_local_var_and_udf_call_chain_share_state_across_iterations(self):
        for v in range(2, 7):
            _, r = self.run_body(v, '''counter() =>
    var value = 0
    value := value + 1
    value
value = for i = 0 to 2
    var local = 0
    local := local + 1
    local * 100 + counter()
plot(value)
plot(counter())''')
            self.assertEqual([o.values for o in r.outputs], [(303,606,909,1212), (1,2,3,4)])

    def test_direct_var_loop_initializer_ends_after_first_iteration(self):
        for v in range(2, 7):
            _, r = self.run_body(v, '''all_iterations() =>
    for i = 1 to 4
        i
var direct = for i = 1 to 4
    i
var wrapped = all_iterations()
plot(direct)
plot(wrapped)''')
            self.assertEqual([o.values for o in r.outputs], [(1,)*4, (4,)*4])

    def test_static_and_runtime_bound_type_and_step_errors(self):
        for v in range(2, 7):
            self.reject(v, 'for i = 0.5 to 2\n    break\nplot(1)', 'LOOP_BOUND_TYPE')
            self.reject(v, 'for i = 0 to 2 by 0\n    break\nplot(1)', 'LOOP_STEP')
            self.reject(v, 'for i = 0 to 2 by -1\n    break\nplot(1)', 'LOOP_STEP')
            self.reject(v, 'step = bar_index\nfor i = 0 to 2 by step\n    break\nplot(1)', 'LOOP_STEP')
            self.reject(v, 'a = for i = 0 to 2\n    "x"\nplot(1)', 'LOOP_RESULT_TYPE')
        self.reject(6, 'a = for i = 0 to 2\n    na\nplot(a)', 'LOOP_RESULT_TYPE')

    def test_control_cannot_escape_a_function_or_appear_outside_loop(self):
        for v in range(1, 7):
            for action in ('break', 'continue'):
                self.reject(v, f'{action}\nplot(1)', 'LOOP_CONTROL_SCOPE')
                self.reject(v, f'bad() =>\n    {action}\n    1\nplot(1)', 'LOOP_CONTROL_SCOPE')

    def test_aggregate_iteration_budget_resets_at_bar_boundary(self):
        with patch('packages.runtime.loops.ITERATIONS_PER_BAR', 8):
            _, r = self.run_body(6, 'sum = 0\nfor i = 0 to 7\n    sum := sum + i\nplot(sum)')
            self.assertEqual(r.outputs[0].values, (28,)*4)
            self.reject(6, 'for i = 0 to 2\n    for j = 0 to 2\n        x = j\nplot(1)', 'ITERATION_BUDGET')
        self.assertEqual((ITERATIONS_PER_BAR, OPERATIONS_PER_BAR), (100000, 1000000))

    def test_operation_budget_and_infinite_loop_do_not_publish_partial_bar(self):
        with patch('packages.runtime.loops.OPERATIONS_PER_BAR', 80):
            self.reject(6, 'sum = 0\nwhile true\n    sum := sum + 1\nplot(sum)', 'OPERATION_BUDGET')
        p = self.session.compile(source(6, 'plot(close)\nwhile true\n    x = 1'))
        ev = self.session.validate_program(p).prepare(p)
        from packages.contracts import BarFrame
        with self.assertRaises(OpenPineError) as caught:ev.step(BarFrame('loops.unit', 0, 0, 10.0))
        self.assertEqual(caught.exception.diagnostic.code, 'ITERATION_BUDGET')
        self.assertEqual((ev.count, ev.outputs, ev.pending_outputs), (0, {}, []))
        self.assertTrue(ev.closed)
        with self.assertRaises(ValueError):ev.checkpoint()

    def test_cancellation_is_checked_inside_a_single_bar(self):
        from packages.execution.driver import HistoricalDriver, MemorySource
        p = self.session.compile(source(6, 'while true\n    x = 1\nplot(1)'))
        ev = self.session.validate_program(p).prepare(p)
        checks = []
        def cancelled():checks.append(True);return len(checks) >= 3
        with self.assertRaises(OpenPineError) as caught:HistoricalDriver(ev, MemorySource(bars())).run(cancelled=cancelled)
        self.assertEqual(caught.exception.diagnostic.category, 'EXECUTION_CANCELLED')
        self.assertIsNotNone(caught.exception.diagnostic.span)
        self.assertLess(ev.work.iterations, 128)
        self.assertEqual(ev.count, 0)

    def test_wire_roundtrip_and_forged_ir_rejected_before_effects(self):
        p = self.session.compile(source(6, 'for i = 0 to 2\n    if i == 1\n        break\nplot(1)'))
        self.assertEqual(decode(encode_ir(p), ProgramIR), p)
        loop = p.statements[1].expression
        bad_counter = replace(loop.counter, value_type=ValueType('float', 'series'))
        control = loop.statements[0].body[0]
        forged = [replace(loop, loop_id='loop:other'), replace(loop, scope_id='scope:root'),
            replace(loop, counter=bad_counter), replace(loop, value_type=ValueType('int', 'series')),
            replace(loop, statements=(replace(loop.statements[0], body=(replace(control, loop_id='other'),)),))]
        for bad in forged:
            with self.assertRaises(OpenPineError):self.session.validate_program(replace(p, statements=(p.statements[0], EvaluateIR(bad, bad.span), p.statements[-1])))
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(p, schema_version='openpine.typed-ir.7'))

    def test_saved_value_loops_decode_optional_recursive_node_union(self):
        bodies = ('value = for i = 0 to 2\n    i\nplot(value)',
            'value = for i = 0 to 2\n    ta.sma(close+i,2)\nplot(value)',
            '[a,b] = for i = 0 to 2\n    [i,close]\nplot(a)\nplot(b)',
            'i = 0\nvalue = while i < 2\n    i := i + 1\n    close+i\nplot(value)',
            'value = for i = 0 to 2\n    for j = 0 to 2\n        i+j\nplot(value)')
        for body in bodies:
            program = self.session.compile(source(6,body))
            restored = decode(encode_ir(program), ProgramIR)
            self.assertEqual(restored, program)
            self.assertEqual(self.session.execute(restored,data=bars()), self.session.execute(program,data=bars()))

    def test_global_only_effects_and_request_boundary_are_preserved(self):
        for v in range(2, 7):
            self.reject(v, 'for i = 0 to 2\n    plot(i)\nplot(1)', 'GLOBAL_ONLY_CALL')
            api = 'security' if v < 5 else 'request.security'
            self.reject(v, f'for i = 0 to 2\n    x = {api}("TEST:X","2",close)\nplot(1)', 'REQUEST_SCOPE')
            self.reject(v, 'for i = 0 to 2\n    x = missing\nplot(1)', 'UNDEFINED_VARIABLE')


if __name__ == '__main__':unittest.main()
