"""Closed coordinate and color profile for finite Pine boxes."""
from . import box_text, drawings
from .box_borders import FIELDS as BORDER_FIELDS, OPERATIONS as BORDER_OPERATIONS, valid_border
from .chart_points import for_drawing
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
MUTATIONS = (*OPERATIONS, 'box.new_xloc', 'box.set_xloc', *for_drawing('box'), *BORDER_OPERATIONS, *box_text.OPERATIONS)
ALL_OPERATIONS = (*MUTATIONS, 'box.get_left', 'box.get_right')


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
    return drawings.uses_drawing(value, 'box', MUTATIONS if calls_only else ALL_OPERATIONS, calls_only)


def validate_defaults(node, version):
    for i, arg in enumerate(node.arguments):
        if arg.origin == 'default' and not (
                node.operation == 'box.new' and i in (4, 5)
                and type(arg.expression) is LiteralIR and arg.expression.value_type == ValueType('color')
                and arg.expression.value == constants(version)['color.blue'][0]):
            raise ValueError('Box default differs from its closed signature.')


def validate_box(value):
    from .drawing_xloc import valid_xloc
    base = {'id', *COORDINATES, 'border_color', 'bgcolor'}
    located = base | {'xloc'}
    styled = located | BORDER_FIELDS
    with_text = styled | box_text.FIELDS
    if type(value) is not dict:
        raise ValueError('Invalid box record.')
    keys = value.keys() - {'force_overlay'}
    if (keys not in (base, located, styled, with_text)
            or 'force_overlay' in value and (value['force_overlay'] is not True or keys != with_text)
            or keys == with_text and not box_text.valid_text_attributes(value)
            or BORDER_FIELDS <= keys and not valid_border(value)
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
        left, right = sorted((left, right))
        intersects = left < end and right >= start
        color = box['border_color']
        mode = box.get('extend', 'none')
        visible_border = color is not None and not color.endswith('00')
        extension = left < right and visible_border and (
            mode in ('left', 'both') and right >= start or mode in ('right', 'both') and left < end)
        if intersects or extension:
            result.append(box)
    return result
