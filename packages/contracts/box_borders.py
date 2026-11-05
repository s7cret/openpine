"""Versioned border appearance and extension for Pine boxes."""
from . import drawings
from .colors import constants as colors
from .drawing_xloc import TOKENS
from .line_extensions import MODES
from .line_styles import DEFAULT_STYLE, DEFAULT_WIDTH, MAX_WIDTH, STYLE_PREFIX, valid_width
from .model import LiteralIR, ValueType
from .wire import digest, read_json

COORDINATE_CONSTRUCTOR = 'box.new_styled'
POINT_CONSTRUCTOR = 'box.new_points_styled'
CONSTRUCTORS = (COORDINATE_CONSTRUCTOR, POINT_CONSTRUCTOR)
STYLES = ('solid', 'dotted', 'dashed')
STYLE_TOKENS = tuple(STYLE_PREFIX + style for style in STYLES)
EXTEND_TOKENS = tuple('extend.' + mode for mode in MODES)
FIELDS = frozenset(('border_width', 'border_style', 'extend'))
SETTERS = {'box.set_border_width': 'border_width', 'box.set_border_style': 'border_style', 'box.set_extend': 'extend'}
OPTIONS = (('border_color', 'color'), ('border_width', 'int'), ('border_style', 'string'),
           ('extend', 'string'), ('xloc', 'string'), ('bgcolor', 'color'))
OPERATIONS = {
    COORDINATE_CONSTRUCTOR: (('left', 'int'), ('top', 'float'), ('right', 'int'), ('bottom', 'float'), *OPTIONS),
    POINT_CONSTRUCTOR: (('top_left', 'chart.point'), ('bottom_right', 'chart.point'), *OPTIONS),
    'box.set_border_width': (('id', 'box'), ('width', 'int')),
    'box.set_border_style': (('id', 'box'), ('style', 'string')),
    'box.set_extend': (('id', 'box'), ('extend', 'string')),
}


def apis(version):
    if version < 4:
        return []
    blue = colors(version)['color.blue'][0]
    defaults = [blue, DEFAULT_WIDTH, STYLE_PREFIX + DEFAULT_STYLE, 'extend.none', TOKENS[0], blue]
    return [
        dict(id=f'openpine.box-borders.v{version}.{op}', name='box.new' if op in CONSTRUCTORS else op, operation=op,
             parameters=[dict(name=name, types=[base], qualifier='const' if name == 'xloc' else 'series')
                         for name, base in parameters], defaults=defaults if op in CONSTRUCTORS else [], named=True,
             result='box' if op in CONSTRUCTORS else 'void', effects=['draws_boxes'])
        for op, parameters in OPERATIONS.items() if version >= 5 or op != POINT_CONSTRUCTOR
    ]


def contracts(version):
    return drawings.contracts(apis(version))


def evidence_version(reference, version):
    return dict(apis=apis(version), styles=list(STYLE_TOKENS) if version >= 4 else [],
                extensions=list(EXTEND_TOKENS) if version >= 4 else [], max_width=MAX_WIDTH if version >= 4 else None,
                reference_sha256=digest(reference))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/box-borders-profile.json')
    if (profile['schema_version'] != 'openpine.box-borders-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != evidence_version(snapshot.references[version], version)):
        raise ValueError('Box border ABI differs from pinned evidence.')


def validate_call(node, version):
    api = next(api for api in apis(version) if api['operation'] == node.operation)
    start = len(api['parameters']) - len(api['defaults'])
    for position, (argument, parameter) in enumerate(zip(node.arguments, api['parameters'], strict=True)):
        expression = argument.expression
        if parameter['name'] == 'xloc' and (type(expression) is not LiteralIR or expression.value not in TOKENS):
            raise ValueError('Expected xloc.bar_index or xloc.bar_time.')
        if argument.origin == 'default' and (position < start or type(expression) is not LiteralIR
                or expression.value_type != ValueType(parameter['types'][0])
                or expression.value != api['defaults'][position - start]):
            raise ValueError('Box border default differs from its closed signature.')


def valid_border(record):
    return (valid_width(record['border_width']) and record['border_style'] in STYLES and record['extend'] in MODES)
