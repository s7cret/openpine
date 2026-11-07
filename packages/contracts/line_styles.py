"""Versioned appearance for Pine line constructors and mutations."""
from . import drawings
from .colors import constants as colors, valid as valid_color
from .drawing_xloc import TOKENS
from .model import LiteralIR, ValueType
from .wire import digest, read_json

COORDINATE_CONSTRUCTOR = 'line.new_styled'
POINT_CONSTRUCTOR = 'line.new_points_styled'
OVERLAY_COORDINATE_CONSTRUCTOR = 'line.new_overlay'
OVERLAY_POINT_CONSTRUCTOR = 'line.new_points_overlay'
POINT_CONSTRUCTORS = (POINT_CONSTRUCTOR, OVERLAY_POINT_CONSTRUCTOR)
OVERLAY_CONSTRUCTORS = (OVERLAY_COORDINATE_CONSTRUCTOR, OVERLAY_POINT_CONSTRUCTOR)
CONSTRUCTORS = (COORDINATE_CONSTRUCTOR, POINT_CONSTRUCTOR, *OVERLAY_CONSTRUCTORS)
STYLES = ('solid', 'dotted', 'dashed', 'arrow_left', 'arrow_right', 'arrow_both')
STYLE_PREFIX = 'line.style_'
MAX_WIDTH = 64
DEFAULT_COLOR = colors(4)['color.blue'][0]
DEFAULT_STYLE = 'solid'
DEFAULT_WIDTH = 1
SETTERS = {'line.set_color': 'color', 'line.set_style': 'style', 'line.set_width': 'width'}
STROKE = (('color', 'color'), ('style', 'string'), ('width', 'int'))
FIELDS = frozenset(name for name, _ in STROKE)
OPTIONS = (('xloc', 'string'), ('extend', 'string'), *STROKE)
COORDINATES = (('x1', 'int'), ('y1', 'float'), ('x2', 'int'), ('y2', 'float'))
POINTS = (('first_point', 'chart.point'), ('second_point', 'chart.point'))
OPERATIONS = {
    COORDINATE_CONSTRUCTOR: (*COORDINATES, *OPTIONS),
    POINT_CONSTRUCTOR: (*POINTS, *OPTIONS),
    **{op: (('id', 'line'), parameter) for op, parameter in zip(SETTERS, STROKE, strict=True)},
    OVERLAY_COORDINATE_CONSTRUCTOR: (*COORDINATES, *OPTIONS, ('force_overlay', 'bool')),
    OVERLAY_POINT_CONSTRUCTOR: (*POINTS, *OPTIONS, ('force_overlay', 'bool')),
}


def constants(version):
    return {STYLE_PREFIX + style: (STYLE_PREFIX + style, 'string') for style in STYLES} if version >= 4 else {}


def apis(version):
    if version < 4:
        return []
    defaults = [TOKENS[0], 'extend.none', colors(version)['color.blue'][0], STYLE_PREFIX + DEFAULT_STYLE, DEFAULT_WIDTH]
    return [
        dict(id=f'openpine.line-styles.v{version}.{op}', name='line.new' if op in CONSTRUCTORS else op, operation=op,
             parameters=[dict(name=name, types=[base], qualifier='const' if name in ('xloc', 'force_overlay') else 'series')
                         for name, base in parameters],
             defaults=[*defaults, False] if op in OVERLAY_CONSTRUCTORS else defaults if op in CONSTRUCTORS else [],
             named=True,
             result='line' if op in CONSTRUCTORS else 'void', effects=['draws_lines'])
        for op, parameters in OPERATIONS.items() if version >= 5 or op not in (*POINT_CONSTRUCTORS, *OVERLAY_CONSTRUCTORS)
    ]


def contracts(version):
    return drawings.contracts(apis(version))


def evidence_version(reference, version):
    return dict(apis=apis(version), constants=list(constants(version)), max_width=MAX_WIDTH if version >= 4 else None,
                reference_sha256=digest(reference))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/line-styles-profile.json')
    if (profile['schema_version'] != 'openpine.line-styles-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != evidence_version(snapshot.references[version], version)):
        raise ValueError('Line style ABI differs from pinned evidence.')


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
            raise ValueError('Line appearance default differs from its closed signature.')


def valid_width(width):
    return type(width) is int and 1 <= width <= MAX_WIDTH


def valid_stroke(record):
    return (valid_color(record['color']) and record['style'] in STYLES and valid_width(record['width']))
