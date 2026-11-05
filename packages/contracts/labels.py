"""Closed ABI and wire records for bar-index labels."""
from . import drawings
from .chart_points import for_drawing
from .drawings import EVENT_LIMIT, LIMIT, valid_id
from .drawing_clock import chart_x
from .model import LiteralIR
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.24'
STATE_SCHEMA = 'openpine.runtime-state.16'
TEXT_LIMIT = 4096
OPERATIONS = {
    'label.new': (('x', 'int'), ('y', 'float'), ('text', 'string')),
    'label.set_xy': (('id', 'label'), ('x', 'int'), ('y', 'float')),
    'label.set_text': (('id', 'label'), ('text', 'string')),
    'label.delete': (('id', 'label'),),
}
ALL_OPERATIONS = (*OPERATIONS, 'label.new_xloc', *for_drawing('label'))


def apis(version):
    return drawings.apis(version, 'label', OPERATIONS, [''])


def contracts(version):
    return drawings.contracts(apis(version))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/labels-profile.json')
    if (profile['schema_version'] != 'openpine.labels-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != dict(apis=apis(version), reference_sha256=digest(snapshot.references[version]))):
        raise ValueError('Label ABI differs from pinned evidence.')


def uses_labels(value, *, calls_only=False):
    return drawings.uses_drawing(value, 'label', ALL_OPERATIONS, calls_only)


def validate_defaults(node):
    for position, arg in enumerate(node.arguments):
        if arg.origin == 'default' and not (
            node.operation == 'label.new' and position == 2
            and type(arg.expression) is LiteralIR and arg.expression.value == ''
        ):
            raise ValueError('Invalid label default argument.')


def validate_label(value):
    from .drawing_xloc import valid_xloc
    if (type(value) is not dict or value.keys() - {'xloc'} != {'id', 'x', 'y', 'text'} or not valid_xloc(value)
            or not valid_id(value['id'])
            or not drawings.valid_index(value['x'])
            or not drawings.valid_price(value['y'])
            or type(value['text']) is not str or len(value['text']) > TEXT_LIMIT):
        raise ValueError('Invalid label record.')


def chart_labels(labels, start, end):
    return [label for label in labels if (x := chart_x(label, 'x')) is not None and start <= x < end and label['y'] is not None]


def validate_snapshot(labels, next_id):
    drawings.validate_snapshot(labels, next_id, 'label', validate_label)
