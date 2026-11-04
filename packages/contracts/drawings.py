"""Shared identity, ABI, and record validation for drawing references."""
import math
from dataclasses import fields, is_dataclass

from .model import CallContract, ValueType
from .wire import digest

PLURALS = {'label': 'labels', 'line': 'lines', 'box': 'boxes', 'table': 'tables', 'linefill': 'linefills'}
BASES = tuple(PLURALS)
KINDS = tuple(PLURALS.values())
JOURNALED_BASES = ('label', 'line', 'box')
JOURNALED_KINDS = tuple(PLURALS[base] for base in JOURNALED_BASES)
EFFECTS = tuple((f'draws_{kind}',) for kind in KINDS)
EVENT_LIMIT = 1024
LIMIT = 50


def apis(version, kind, operations, defaults):
    if version < 4:
        return []
    plural = PLURALS[kind]
    return [
        dict(id=f'openpine.{plural}.v{version}.{name}', name=name, operation=name,
             parameters=[dict(name=n, types=[t], qualifier='series') for n, t in parameters],
             defaults=defaults if name == kind + '.new' else [], named=True,
             result=kind if name == kind + '.new' else 'void', effects=[f'draws_{plural}'])
        for name, parameters in operations.items()
    ]


def contracts(apis):
    return {
        a['id']: CallContract(
            'symbol.' + a['id'], 'overload.' + a['id'], 'assertion.' + a['id'], digest(a), a['operation'],
            tuple('parameter.' + a['id'] + '.' + p['name'] for p in a['parameters']),
            tuple(tuple(p['types']) for p in a['parameters']),
            ('series',) * len(a['parameters']), None, tuple(a['effects']),
            ValueType(a['result'], 'const' if a['result'] == 'void' else 'series'), 'fixed')
        for a in apis
    }


def uses_drawing(value, kind, operations, calls_only):
    contract = getattr(value, 'contract', None)
    if getattr(value, 'operation', None) in operations or getattr(contract, 'operation', None) in operations:
        return True
    if not calls_only and type(value) is ValueType and value.base == kind:
        return True
    if type(value) is tuple:
        return any(uses_drawing(item, kind, operations, calls_only) for item in value)
    return is_dataclass(value) and any(
        uses_drawing(getattr(value, f.name), kind, operations, calls_only) for f in fields(value))


def valid_id(value):
    return type(value) is int and 0 < value < 2**63 - 1


def valid_index(value):
    return value is None or type(value) is int and -(2**63) <= value < 2**63


def valid_price(value):
    return value is None or type(value) is float and math.isfinite(value)


def validate_snapshot(values, next_id, kind, validate):
    if type(values) is not list or len(values) > LIMIT:
        raise ValueError(f'Invalid live {kind} snapshot.')
    previous = 0
    for value in values:
        validate(value)
        if not previous < value['id'] < next_id:
            raise ValueError(f'Invalid {kind} identity order.')
        previous = value['id']
