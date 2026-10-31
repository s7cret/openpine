"""Bind the finite array intrinsic family; evaluate source arguments only once."""

from packages.contracts import ValueType
from packages.contracts.arrays import ELEMENTS, ARRAY_BASES, OPERATIONS, parameters, result_type,is_array
from packages.contracts.objects import is_object
from packages.contracts.enums import is_enum
from .binder import BoundArgument, BoundArray, BoundNA, BoundLiteral


def bind_is_na(binder, call, expression):
    if call.arguments[0].name not in (None, 'x'):
        binder.fail('LANGUAGE_INVALID', 'UNKNOWN_ARGUMENT', 'Expected na(x).', call.span)
    return BoundArray('is_na', expression.value_type.base.removeprefix('array.'),
        (BoundArgument('id', expression, 'source', 0),), (0,), ValueType('bool', 'series'), call.span, binder.namespace)


def bind(binder, call, *, prepared=None):
    fail = lambda code, text: binder.fail('LANGUAGE_INVALID', code, text, call.span)
    version = binder.catalog.language_version
    if version < 4:fail('ARRAY_VERSION', 'Arrays require Pine v4 or later.')
    if not getattr(binder.catalog.snapshot, 'arrays', None):
        binder.fail('PROFILE_UNCOVERED', 'ARRAY_PROFILE', 'Array policy is not pinned.', call.span)
    name = call.name.removeprefix('array.')
    element = None
    if name.startswith('new<'):
        if version < 5:fail('ARRAY_SYNTAX_VERSION', 'Generic constructors require Pine v5 or v6.')
        element, op = binder.type_base(name[4:-1]), 'new'
    elif name.startswith('new_'):
        element, op = name[4:], 'new'
    else:op = name
    if op not in OPERATIONS or op == 'is_na' or element is not None and element not in ELEMENTS and not is_object(element) and not is_enum(element):
        binder.fail('PROFILE_UNCOVERED', 'ARRAY_OPERATION', 'Array operation/element is outside this profile.', call.span)
    expressions = tuple(binder.expression(a.expression) for a in call.arguments) if prepared is None else prepared
    if op == 'from':
        if not 1 <= len(expressions) <= 32:fail('ARRAY_ARITY', 'array.from requires 1..32 scalar values.')
        if any(a.name is not None for a in call.arguments):fail('UNKNOWN_ARGUMENT', 'array.from accepts positional values.')
        bases = {e.value_type.base for e in expressions}
        if bases <= {'int', 'float', 'na'} and bases != {'na'}:
            element = 'float' if 'float' in bases else 'int'
        elif len(bases) == 1 and bases <= set(ELEMENTS):element = next(iter(bases))
        elif len(bases-{'na'})==1 and (is_object(next(iter(bases-{'na'}))) or is_enum(next(iter(bases-{'na'})))):element=next(iter(bases-{'na'}))
        else:fail('ARRAY_ELEMENT_TYPE', 'Array elements must have one resolved scalar type.')
    names = parameters(op, len(expressions))
    bound, positional, named = {}, 0, False
    for index, (argument, expression) in enumerate(zip(call.arguments, expressions, strict=True)):
        if argument.name is None:
            if named:fail('POSITIONAL_AFTER_NAMED', 'Positional arguments must precede named arguments.')
            position = positional;positional += 1
        else:
            named = True
            if argument.name not in names:fail('UNKNOWN_ARGUMENT', 'Unknown array argument.')
            position = names.index(argument.name)
        if position >= len(names):fail('TOO_MANY_ARGUMENTS', 'Too many array arguments.')
        if position in bound:fail('DUPLICATE_ARGUMENT', 'Array argument supplied twice.')
        bound[position] = BoundArgument(names[position], expression, 'source', index)
    for position, name in enumerate(names):
        if position not in bound:
            if op != 'new' or position == 0 and version == 4:fail('MISSING_ARGUMENT', 'Missing array argument: ' + name)
            if position == 0:expression = BoundLiteral(0, ValueType('int'), call.span)
            elif element == 'bool' and version == 6:expression = BoundLiteral(False, ValueType('bool'), call.span)
            else:expression = BoundNA(ValueType(element,'series' if is_object(element) else 'const'), call.span)
            bound[position] = BoundArgument(name, expression, 'default', None)
    if op not in ('new', 'from'):
        base = bound[0].expression.value_type.base
        if not is_array(base):fail('ARRAY_TYPE', 'Array operation requires an array reference.')
        element = base.removeprefix('array.')
    if is_object(element):
        from .specification import ROOT
        from packages.contracts.object_arrays import admit
        if version<5:fail('UDT_ARRAY_VERSION','Object arrays require Pine v5/v6.')
        if element not in {binder.type_base(n) for n in binder.shared.get('object_definitions',{})}:fail('UDT_ARRAY_TYPE','Unknown array element type.')
        try:admit(ROOT,binder.catalog.snapshot,version)
        except ValueError as e:binder.fail('SPEC_UNRESOLVED','UDT_ARRAY_PROFILE',str(e),call.span)
    if op in ('sum', 'avg', 'min', 'max') and element not in ('int', 'float'):
        fail('ARRAY_NUMERIC', 'Array statistics require numeric elements.')
    for position, argument in list(bound.items()):
        name, expression = argument.parameter_id, argument.expression
        if name in ('size', 'index'):expression = binder.coerce(expression, 'int', 'ARRAY_INDEX_TYPE')
        elif op == 'from' or name in ('initial_value', 'value'):
            if isinstance(expression, BoundNA) and element == 'string':expression = BoundNA(ValueType('string'), expression.span)
            else:expression = binder.coerce(expression, element, 'ARRAY_ELEMENT_TYPE')
        bound[position] = BoundArgument(name, expression, argument.origin, argument.source_index)
    args = tuple(bound[i] for i in range(len(names)))
    order = tuple(sorted(range(len(args)), key=lambda i: (args[i].origin == 'default',
        args[i].source_index if args[i].source_index is not None else i)))
    return BoundArray(op, element, args, order, result_type(op, element), call.span, binder.namespace)
