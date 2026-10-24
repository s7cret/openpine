"""Independent primitive-array expectations, version gates and hostile IR."""

import copy
from dataclasses import replace
import unittest

from packages.contracts import HISTORICAL_PROFILE_ID, OpenPineError, ArrayIR, EvaluateIR, StoreIR, ValueType, LiteralIR
from packages.contracts.codec import encode, decode
from packages.contracts import ProgramIR
from packages.execution.session import Session
from test_loops import bars


def source(version, body):
    return f'//@version={version}\n' + ('study' if version < 5 else 'indicator') + '("arrays")\n' + body + '\n'


class ArrayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.session = Session(profile_id=HISTORICAL_PROFILE_ID)

    def run_body(self, body, version=6):
        return self.session.run(source(version, body), data=bars())[1]

    def values(self, body, version=6):return [o.values for o in self.run_body(body, version).outputs]

    def rejected(self, body, code, version=6):
        with self.assertRaises(OpenPineError) as caught:self.session.compile(source(version,body))
        self.assertEqual(caught.exception.diagnostic.code,code)
        self.assertIsNotNone(caught.exception.diagnostic.span)

    def test_reference_alias_and_independent_copy(self):
        for v in (4,5,6):
            result = self.values('''var a = array.new_int(1, 0)
b = a
array.set(b, 0, array.get(b, 0) + 1)
c = array.copy(a)
array.set(c, 0, 99)
plot(array.get(a, 0))
plot(array.get(c, 0))''',v)
            self.assertEqual(result,[(1,2,3,4),(99,99,99,99)])

    def test_queue_stack_and_clear(self):
        self.assertEqual(self.values('''a = array.from(2, 3)
array.unshift(a, 1)
array.push(a, 4)
first = array.shift(a)
last = array.pop(a)
plot(first * 100 + last * 10 + array.sum(a))
array.clear(a)
plot(array.size(a))'''),[(145,)*4,(0,)*4])

    def test_numeric_statistics_skip_missing_and_preserve_types(self):
        result=self.run_body('''a = array.from(2, na, 4)
plot(array.sum(a))
plot(array.avg(a))
plot(array.min(a))
plot(array.max(a))
b = array.new_float(2)
plot(array.sum(b))
array.clear(b)
plot(array.avg(b))''')
        self.assertEqual([o.values for o in result.outputs],[(6,)*4,(3.,)*4,(2,)*4,(4,)*4,(None,)*4,(None,)*4])
        self.assertEqual([o.value_type.base for o in result.outputs],['int','float','int','int','float','float'])

    def test_from_promotes_numeric_elements(self):
        self.assertEqual(self.values('a = array.from(1, 2.5)\nplot(array.get(a, 0))\nplot(array.sum(a))'),[(1.,)*4,(3.5,)*4])

    def test_primitive_bool_string_and_default_elements(self):
        for v in (4,5,6):
            self.assertEqual(self.values('''a = array.from("one", "two")
array.set(a, 1, "three")
b = array.new_bool(1)
c = array.new_string(1)
plot(array.get(a, 1) == "three" ? 1 : 0)
plot(array.get(b, 0) == false ? 1 : 0)
plot(array.get(c, 0) == "" ? 1 : 0)
plot(na(array.get(c, 0)) ? 1 : 0)''',v),[(1,)*4,((1 if v==6 else 0),)*4,(0,)*4,(1,)*4])

    def test_named_arguments_keep_source_evaluation_order(self):
        body='''var n = array.new_int(1, 0)
tick() =>
    value = array.get(n, 0)
    array.set(n, 0, value + 1)
    value
a = array.new_int(initial_value=tick(), size=tick())
plot(array.size(a))
plot(array.get(a, 0))'''
        self.assertEqual(self.values(body),[(1,3,5,7),(0,2,4,6)])

    def test_typed_missing_reference_and_conditional_selection(self):
        self.assertEqual(self.values('''var array<float> a = na
if na(a)
    a := array.new<float>(1, close)
b = close > 20 ? a : array.new<float>(1, 0.0)
plot(array.get(b, 0))'''),[(0.,0.,10.,10.)])

    def test_functions_mutate_contents_and_return_the_same_object(self):
        for v in (4,5,6):
            annotation='float[]' if v==4 else 'array<float>'
            body=f'''var a = array.new_float(0)
append({annotation} values) =>
    array.push(values, close)
    values
b = append(a)
plot(array.size(b))
plot(array.avg(a))'''
            self.assertEqual(self.values(body,v),[(1,2,3,4),(10.,15.,20.,25.)])

    def test_function_can_mutate_a_captured_array(self):
        self.assertEqual(self.values('''var a = array.new_int(1, 0)
increment() =>
    array.set(a, 0, array.get(a, 0) + 1)
    array.get(a, 0)
plot(increment())'''),[(1,2,3,4)])

    def test_missing_value_if_returns_a_missing_reference(self):
        self.assertEqual(self.values('''a = if close > 20
    array.from(close)
plot(na(a) ? 0.0 : array.get(a, 0))'''),[(0.,0.,30.,40.)])

    def test_history_is_an_object_identity_not_a_deep_snapshot(self):
        self.assertEqual(self.values('''var a = array.new_float(1, 0.0)
array.set(a, 0, close)
p = a[1]
plot(na(p) ? na : array.get(p, 0))'''),[(None,20.,30.,40.)])
        self.assertEqual(self.values('''a = array.new_float(1, close)
p = a[1]
plot(na(p) ? na : array.get(p, 0))'''),[(None,10.,20.,30.)])

    def test_for_in_values_indices_discard_and_loop_result(self):
        for v in (5,6):
            self.assertEqual(self.values('''a = array.from(1, 2, 3)
sum = 0
for [i, value] in a
    sum := sum + i * value
value = for [_, item] in a
    item * 10
plot(sum)
plot(value)''',v),[(8,)*4,(30,)*4])

    def test_for_in_reads_live_size_and_evaluates_collection_once(self):
        self.assertEqual(self.values('''a = array.from(1, 2)
sum = 0
for value in a
    sum := sum + value
    if array.size(a) < 4
        array.push(a, 3)
    a := array.from(99)
plot(sum)
plot(array.size(a))'''),[(6,)*4,(1,)*4])

    def test_for_in_clear_and_removal_change_remaining_iteration_count(self):
        self.assertEqual(self.values('''a = array.from(1, 2, 3, 4)
sum = 0
for item in a
    sum := sum + item
    array.pop(a)
plot(sum)
plot(array.size(a))'''),[(3,)*4,(2,)*4])

    def test_for_in_break_continue_empty_and_nearest_target(self):
        self.assertEqual(self.values('''a = array.from(1, 2, 3, 4)
sum = 0
for item in a
    if item == 2
        continue
    if item == 4
        break
    for inner in a
        sum := sum + item
        break
empty = array.new_int()
value = for item in empty
    item
plot(sum)
plot(value)'''),[(4,)*4,(None,)*4])

    def test_versioned_syntax_availability(self):
        for v in (1,2,3):self.rejected('a = array.new_int(1)\nplot(1)','ARRAY_VERSION',v)
        self.rejected('a = array.new<int>(1)\nplot(1)','ARRAY_SYNTAX_VERSION',4)
        self.rejected('array<int> a = array.new_int(1)\nplot(1)','ARRAY_SYNTAX_VERSION',4)
        self.rejected('a = array.new_int(0)\nfor x in a\n    y = x\nplot(1)','FOR_IN_VERSION',4)
        for v in (5,6):self.assertEqual(self.values('array<int> a = array.new<int>()\nplot(array.size(a))',v),[(0,)*4])

    def test_negative_indices_are_versioned(self):
        body='a = array.from(1, 2)\narray.set(a, -1, 7)\nplot(array.get(a, -2))\nplot(array.sum(a))'
        self.assertEqual(self.values(body),[(1,)*4,(8,)*4])
        for v in (4,5):
            with self.assertRaises(OpenPineError) as caught:self.values(body,v)
            self.assertEqual(caught.exception.diagnostic.code,'ARRAY_INDEX')

    def test_explicit_errors_for_invalid_array_shapes(self):
        for body,code in [
            ('a = array.from()\nplot(1)','ARRAY_ARITY'),
            ('a = array.from(na)\nplot(1)','ARRAY_ELEMENT_TYPE'),
            ('a = array.from(1, "x")\nplot(1)','ARRAY_ELEMENT_TYPE'),
            ('a = array.from(1)\narray.push(a, 1.5)\nplot(1)','ARRAY_ELEMENT_TYPE'),
            ('a = array.from(true)\nplot(array.sum(a))','ARRAY_NUMERIC'),
            ('a = array.from(1)\nplot(array.min(a, 1))','TOO_MANY_ARGUMENTS'),
            ('a = array.from(1)\narray.sort(a)\nplot(1)','ARRAY_OPERATION'),
            ('a = array.new_int(1.5)\nplot(1)','ARRAY_INDEX_TYPE'),
            ('a = array.from(1)\nplot(a == a ? 1 : 0)','ARRAY_OPERATOR'),
            ('a = array.from(1)\nfor [i,i] in a\n    x = i\nplot(1)','FOR_IN_TARGET'),
            ('for item in close\n    x = item\nplot(1)','FOR_IN_TYPE'),
            ('a = array.from(1)\nfor item in a\n    item := 3\nplot(1)','LOOP_COUNTER_WRITE'),
        ]:
            with self.subTest(code=code):self.rejected(body,code)

    def test_parameter_and_global_rebinding_remain_forbidden(self):
        self.rejected('''var a = array.from(1)
f(values) =>
    values := array.from(2)
    1
plot(f(a))''','FUNCTION_WRITE')

    def test_array_ir_roundtrip_and_whole_program_admission(self):
        program=self.session.compile(source(6,'a = array.from(1, 2)\narray.push(a, 3)\nplot(array.sum(a))'))
        self.assertEqual(decode(encode(program),ProgramIR),program)
        store=program.statements[1];operation=store.expression
        for changed in (replace(operation,element='float'),replace(operation,evaluation_order=(1,0)),
                replace(operation,value_type=ValueType('array.int','const')),
                replace(operation,call_site_id='invented'),replace(operation,operation='clear')):
            bad=replace(program,statements=(program.statements[0],replace(store,expression=changed),*program.statements[2:]))
            with self.assertRaises(OpenPineError):self.session.validate_program(bad)

    def test_historical_profile_and_array_schema_are_required(self):
        program=self.session.compile(source(6,'a = array.from(1)\nplot(array.size(a))'))
        with self.assertRaises(OpenPineError):self.session.validate_program(replace(program,schema_version='openpine.typed-ir.8'))
        literal=Session();p=literal.compile(source(6,'a = array.from(1)\nplot(array.size(a))'))
        with self.assertRaises(OpenPineError):literal.validate_program(p)

    def test_for_in_saved_targets_and_collection_are_checked_before_execution(self):
        program=self.session.compile(source(6,'a = array.from(1, 2)\nx = 0\nfor [i,v] in a\n    x := x + i * v\nplot(x)'))
        loop=program.statements[3].expression
        for changed in (replace(loop,loop_id='invented'),replace(loop,targets=(loop.targets[0],loop.targets[0])),
                replace(loop,collection=LiteralIR(1,ValueType('int'),loop.span)),
                replace(loop,value_type=ValueType('float','series'))):
            bad=replace(program,statements=(*program.statements[:3],replace(program.statements[3],expression=changed),program.statements[4]))
            with self.assertRaises(OpenPineError):self.session.validate_program(bad)

    def test_constructor_defaults_cannot_be_replaced_in_saved_ir(self):
        program=self.session.compile(source(6,'a = array.new_bool()\nplot(array.size(a))'))
        store=program.statements[1];operation=store.expression
        for index,value in ((0,1),(1,True)):
            args=list(operation.arguments);args[index]=replace(args[index],expression=replace(args[index].expression,value=value))
            bad=replace(program,statements=(program.statements[0],replace(store,expression=replace(operation,arguments=tuple(args))),program.statements[2]))
            with self.assertRaises(OpenPineError):self.session.validate_program(bad)

    def test_tuple_arguments_to_scalar_builtins_produce_a_pine_diagnostic(self):
        for version in range(1,7):
            prefix='math.' if version>=5 else ''
            with self.subTest(version=version):
                self.rejected(f'plot({prefix}min([1, 2], [3, 4]))','NO_MATCHING_OVERLOAD',version)

    def test_invalid_runtime_indices_sizes_empty_and_missing_references(self):
        for body,code in [('a = array.from(1)\nplot(array.get(a, 1))','ARRAY_INDEX'),
            ('a = array.new_int(-1)\nplot(1)','ARRAY_SIZE'),
            ('a = array.new_int(0)\nplot(array.pop(a))','ARRAY_EMPTY'),
            ('array<float> a = na\nplot(array.size(a))','ARRAY_NA'),
            ('a = array.from(1)\nplot(array.get(a, na))','ARRAY_INDEX')]:
            with self.subTest(code=code):
                with self.assertRaises(OpenPineError) as caught:self.values(body)
                self.assertEqual(caught.exception.diagnostic.code,code)
                self.assertIsNotNone(caught.exception.diagnostic.span)

    def test_array_spec_is_pinned_and_reproducible(self):
        from tools.array_spec import prepare, RELEASE
        from tools.specification import ROOT
        for name,raw in prepare().items():self.assertEqual((ROOT/'spec/bundles'/RELEASE/name).read_bytes(),raw)
        for v in range(1,7):self.assertEqual(self.session.snapshot.arrays['versions'][str(v)]['policy']['available'],v>=4)


if __name__=='__main__':unittest.main()
