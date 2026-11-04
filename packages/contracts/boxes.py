"""Closed coordinate and color profile for finite Pine boxes."""
from . import drawings
from .colors import constants, valid
from .drawing_clock import chart_x
from .model import LiteralIR, ValueType
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.26'
STATE_SCHEMA = 'openpine.runtime-state.18'
COORDINATES = ('left', 'top', 'right', 'bottom')
OPERATIONS = {
    'box.new': (('left', 'int'), ('top', 'float'), ('right', 'int'), ('bottom', 'float'),
                ('border_color', 'color'), ('bgcolor', 'color')),
    'box.set_lefttop': (('id', 'box'), ('left', 'int'), ('top', 'float')),
    'box.set_rightbottom': (('id', 'box'), ('right', 'int'), ('bottom', 'float')),
    'box.set_border_color': (('id', 'box'), ('color', 'color')),
    'box.set_bgcolor': (('id', 'box'), ('color', 'color')),
    'box.delete': (('id', 'box'),),
}
ALL_OPERATIONS = (*OPERATIONS, 'box.new_xloc')


def apis(version):
    return drawings.apis(version, 'box', OPERATIONS, [constants(version)['color.blue'][0]] * 2) if version >= 4 else []


def contracts(version):
    return drawings.contracts(apis(version))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/boxes-profile.json')
    if (profile['schema_version'] != 'openpine.boxes-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != dict(apis=apis(version), reference_sha256=digest(snapshot.references[version]))):
        raise ValueError('Box ABI differs from pinned evidence.')


def uses_boxes(value, *, calls_only=False):
    return drawings.uses_drawing(value, 'box', ALL_OPERATIONS, calls_only)


def validate_defaults(node, version):
    for i, arg in enumerate(node.arguments):
        if arg.origin == 'default' and not (
                node.operation == 'box.new' and i in (4, 5)
                and type(arg.expression) is LiteralIR and arg.expression.value_type == ValueType('color')
                and arg.expression.value == constants(version)['color.blue'][0]):
            raise ValueError('Box default differs from its closed signature.')


def validate_box(value):
    from .drawing_xloc import valid_xloc
    if (type(value) is not dict or value.keys() - {'xloc'} != {'id', *COORDINATES, 'border_color', 'bgcolor'}
            or not valid_xloc(value)
            or not drawings.valid_id(value['id'])
            or not drawings.valid_index(value['left']) or not drawings.valid_index(value['right'])
            or not drawings.valid_price(value['top']) or not drawings.valid_price(value['bottom'])
            or not valid(value['border_color']) or not valid(value['bgcolor'])):
        raise ValueError('Invalid box record.')


def chart_boxes(boxes, start, end):
    if end <= start:
        return []
    result = []
    for box in boxes:
        left, right = chart_x(box, 'left'), chart_x(box, 'right')
        top, bottom = box['top'], box['bottom']
        if None in (left, top, right, bottom) or (left, top) == (right, bottom):
            continue
        if min(left, right) < end and max(left, right) >= start:
            result.append(box)
    return result
