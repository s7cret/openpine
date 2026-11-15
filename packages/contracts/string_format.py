"""Bounded arities for the versioned Pine str.format call."""

from functools import cache
from types import MappingProxyType

from .model import CallContract, ValueType
from .wire import digest


NAME = 'str.format'
OPERATION = 'strings.format'
MAX_VALUES = 32
VERSIONS = (4, 5, 6)
SCALARS = ('int', 'float', 'bool', 'string')


@cache
def parameter_names(count):
    return ('formatString', *(f'arg{index}' for index in range(count)))


@cache
def contracts(version):
    if version not in VERSIONS:
        return MappingProxyType({})
    result = {}
    for count in range(MAX_VALUES + 1):
        names = parameter_names(count)
        identifier = f'openpine.string-format.v{version}.{count}'
        types = (('string',), *((SCALARS,) * count))
        signature = {'name': NAME, 'parameters': names, 'types': types,
                     'result': 'scalar_simple', 'format': 'number_and_scalar_placeholders.1'}
        contract = CallContract('symbol.' + identifier, 'overload.' + identifier,
                                'assertion.' + identifier, digest(signature), OPERATION,
                                tuple(f'parameter.{identifier}.{name}' for name in names),
                                types, ('series',) * len(names), None, (), ValueType('string'), 'scalar_simple')
        result[contract.overload_id] = contract
    return MappingProxyType(result)
