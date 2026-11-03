"""Closed ABI and wire records for bar-index labels."""
import math
from dataclasses import fields, is_dataclass

from .model import CallContract, LiteralIR, ValueType
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.24'
STATE_SCHEMA = 'openpine.runtime-state.16'
LABEL = ValueType('label', 'series')
LIMIT = 50
TEXT_LIMIT = 4096
EVENT_LIMIT = 1024
OPERATIONS = {
    'label.new': (('x', 'int'), ('y', 'float'), ('text', 'string')),
    'label.set_xy': (('id', 'label'), ('x', 'int'), ('y', 'float')),
    'label.set_text': (('id', 'label'), ('text', 'string')),
    'label.delete': (('id', 'label'),),
}


def apis(version):
    if version < 4:
        return []
    return [
        dict(id=f'openpine.labels.v{version}.{name}', name=name, operation=name,
             parameters=[dict(name=n, types=[t], qualifier='series') for n, t in parameters],
             defaults=[''] if name == 'label.new' else [], named=True,
             result='label' if name == 'label.new' else 'void', effects=['draws_labels'])
        for name, parameters in OPERATIONS.items()
    ]


def contracts(version):
    return {
        a['id']: CallContract(
            'symbol.' + a['id'], 'overload.' + a['id'], 'assertion.' + a['id'], digest(a), a['operation'],
            tuple('parameter.' + a['id'] + '.' + p['name'] for p in a['parameters']),
            tuple(tuple(p['types']) for p in a['parameters']),
            ('series',) * len(a['parameters']), None, tuple(a['effects']),
            LABEL if a['name'] == 'label.new' else ValueType('void'), 'fixed')
        for a in apis(version)
    }


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/labels-profile.json')
    if (profile['schema_version'] != 'openpine.labels-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != dict(apis=apis(version), reference_sha256=digest(snapshot.references[version]))):
        raise ValueError('Label ABI differs from pinned evidence.')


def uses_labels(value, *, calls_only=False):
    contract = getattr(value, 'contract', None)
    if getattr(value, 'operation', None) in OPERATIONS or getattr(contract, 'operation', None) in OPERATIONS:
        return True
    if not calls_only and value == LABEL:
        return True
    if type(value) is tuple:
        return any(uses_labels(item, calls_only=calls_only) for item in value)
    return is_dataclass(value) and any(uses_labels(getattr(value, f.name), calls_only=calls_only) for f in fields(value))


def validate_defaults(node):
    for position, arg in enumerate(node.arguments):
        if arg.origin == 'default' and not (
            node.operation == 'label.new' and position == 2
            and type(arg.expression) is LiteralIR and arg.expression.value == ''
        ):
            raise ValueError('Invalid label default argument.')


def valid_id(value):
    return type(value) is int and 0 < value < 2**63 - 1


def validate_label(value):
    if (type(value) is not dict or value.keys() != {'id', 'x', 'y', 'text'}
            or not valid_id(value['id'])
            or value['x'] is not None and (type(value['x']) is not int or not -(2**63) <= value['x'] < 2**63)
            or value['y'] is not None and (type(value['y']) is not float or not math.isfinite(value['y']))
            or type(value['text']) is not str or len(value['text']) > TEXT_LIMIT):
        raise ValueError('Invalid label record.')


def chart_labels(labels, start, end):
    return [label for label in labels if label['x'] is not None and start <= label['x'] < end and label['y'] is not None]


def validate_snapshot(labels, next_id):
    if type(labels) is not list or len(labels) > LIMIT:
        raise ValueError('Invalid live label snapshot.')
    previous = 0
    for label in labels:
        validate_label(label)
        if not previous < label['id'] < next_id:
            raise ValueError('Invalid label identity order.')
        previous = label['id']
