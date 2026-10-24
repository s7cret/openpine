"""Closed array ABI and the bounded, versioned reference-object profile."""

from dataclasses import fields, is_dataclass
from .model import ValueType, ArrayIR, ForInIR, NAIR, LiteralIR

ELEMENTS = ('int', 'float', 'bool', 'string')
ARRAY_BASES = tuple('array.' + element for element in ELEMENTS)
OPERATIONS = ('new', 'from', 'size', 'get', 'set', 'push', 'pop', 'shift',
              'unshift', 'clear', 'copy', 'sum', 'avg', 'min', 'max', 'is_na')
MAX_LENGTH = 100000
MAX_OBJECTS = 100000
MAX_CELLS = 1000000
MAX_STRING_BYTES = 16 * 1024 * 1024


def array_policy(version):
    return {'available': version >= 4, 'for_in': version >= 5,
        'generic_syntax': version >= 5, 'negative_indices': version == 6,
        'elements': list(ELEMENTS), 'assignment': 'reference', 'copy': 'independent',
        'reference_qualifier': 'series', 'history': 'past_object_id_not_value_snapshot',
        'for_in_collection': 'evaluated_once', 'for_in_size': 'before_each_iteration',
        'for_in_targets': 'immutable_local_value_or_index_value',
        'missing_bool_element': 'false' if version == 6 else 'na',
        'statistics': 'ignore_na_empty_or_all_na_returns_na',
        'sum_result': 'element_type', 'avg_result': 'float', 'min_max': 'first_rank_only',
        'from_arity': [1, 32], 'max_length': MAX_LENGTH, 'max_objects': MAX_OBJECTS,
        'max_cells': MAX_CELLS, 'max_string_bytes': MAX_STRING_BYTES,
        'gc': 'completed_bar_persistent_and_history_roots',
        'preview': 'copy_on_write_objects', 'checkpoint': 'closed_heap_and_typed_references',
        'external_execution_verified': False}


def array_apis(version):
    if version < 4:return []
    result = [{'name':'array.new_' + element,'operation':'new','element':element,
        'parameters':['size','initial_value'],'size_optional':version >= 5} for element in ELEMENTS]
    if version >= 5:result.append({'name':'array.new<type>','operation':'new','elements':list(ELEMENTS),
        'parameters':['size','initial_value'],'size_optional':True})
    result.append({'name':'array.from','operation':'from','arity':[1,32],'element_inference':'homogeneous_with_int_float_promotion'})
    for op in OPERATIONS:
        if op in ('new','from'):continue
        result.append({'name':'na' if op == 'is_na' else 'array.'+op,'operation':op,
            'parameters':list(parameters(op)), 'elements':['int','float'] if op in ('sum','avg','min','max') else list(ELEMENTS)})
    return result


def uses_arrays(value):
    if isinstance(value, (ArrayIR, ForInIR)): return True
    if isinstance(value, ValueType) and value.base in ARRAY_BASES: return True
    if is_dataclass(value): return any(uses_arrays(getattr(value, f.name)) for f in fields(value))
    return type(value) is tuple and any(map(uses_arrays, value))


def parameters(operation, arity=0):
    if operation == 'from': return tuple(f'arg{i}' for i in range(arity))
    if operation == 'new': return ('size', 'initial_value')
    if operation == 'get': return ('id', 'index')
    if operation == 'set': return ('id', 'index', 'value')
    if operation in ('push', 'unshift'): return ('id', 'value')
    return ('id',)


def result_type(operation, element):
    if operation in ('new', 'from', 'copy'): return ValueType('array.' + element, 'series')
    if operation in ('set', 'push', 'unshift', 'clear'): return ValueType('void')
    if operation == 'size': return ValueType('int', 'series')
    if operation == 'is_na': return ValueType('bool', 'series')
    return ValueType('float' if operation == 'avg' else element, 'series')


def validate_operation(node, version):
    """Recompute the complete ABI; saved IR cannot choose arbitrary effects/types."""
    if version < 4 or node.operation not in OPERATIONS or node.element not in ELEMENTS:
        raise ValueError('Array operation/version/element is unavailable.')
    op, element, args = node.operation, node.element, node.arguments
    if op == 'from' and not 1 <= len(args) <= 32: raise ValueError('Array.from requires 1..32 scalar arguments.')
    names = parameters(op, len(args))
    if len(args) != len(names) or tuple(a.parameter_id for a in args) != names:
        raise ValueError('Array parameters differ from the closed signature.')
    if any(a.origin not in ('source', 'default') or a.origin == 'default' and a.source_index is not None for a in args):
        raise ValueError('Invalid array argument origin.')
    indices = [a.source_index for a in args if a.origin == 'source']
    if any(type(i) is not int for i in indices) or sorted(indices) != list(range(len(indices))):
        raise ValueError('Invalid array source order.')
    order = tuple(sorted(range(len(args)), key=lambda i: (args[i].origin == 'default',
        args[i].source_index if args[i].source_index is not None else i)))
    if node.evaluation_order != order: raise ValueError('Array argument order differs.')
    if op != 'new' and any(a.origin != 'source' for a in args): raise ValueError('Only constructors have defaults.')
    if op == 'new':
        for index, arg in enumerate(args):
            if arg.origin != 'default':continue
            expected = (isinstance(arg.expression, LiteralIR) and arg.expression.value_type == ValueType('int') and type(arg.expression.value) is int and arg.expression.value == 0 and version >= 5) if index == 0 else (
                isinstance(arg.expression, LiteralIR) and arg.expression.value_type == ValueType('bool') and arg.expression.value is False if element == 'bool' and version == 6 else
                isinstance(arg.expression, NAIR) and arg.expression.value_type == ValueType(element))
            if not expected:raise ValueError('Array constructor default differs from its versioned signature.')
    if op == 'new': types = ('int', element)
    elif op == 'from': types = (element,) * len(args)
    elif op == 'get': types = ('array.' + element, 'int')
    elif op == 'set': types = ('array.' + element, 'int', element)
    elif op in ('push', 'unshift'): types = ('array.' + element, element)
    elif op == 'is_na' and element == 'string' and args[0].expression.value_type.base == 'string':types = ('string',)
    else: types = ('array.' + element,)
    if tuple(a.expression.value_type.base for a in args) != types:
        raise ValueError('Array element/argument types differ.')
    if op in ('sum', 'avg', 'min', 'max') and element not in ('int', 'float'):
        raise ValueError('Array statistics require numeric elements.')
    if node.value_type != result_type(op, element): raise ValueError('Array result type differs.')
