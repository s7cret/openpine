"""Versioned appearance for price-anchored labels."""
from . import drawings
from .colors import constants as colors, valid as valid_color
from .drawing_xloc import TOKENS
from .markers import SIZES
from .model import LiteralIR, ValueType
from .wire import digest, read_json

COORDINATE_CONSTRUCTOR = 'label.new_styled'
POINT_CONSTRUCTOR = 'label.new_points_styled'
CONSTRUCTORS = (COORDINATE_CONSTRUCTOR, POINT_CONSTRUCTOR)
PRICE = 'yloc.price'
LOCATIONS = (PRICE, 'yloc.abovebar', 'yloc.belowbar')
STYLES = {'label.style_none': 'none', **{
    'label.style_label_' + direction: direction for direction in ('center', 'up', 'down', 'left', 'right')}}
DEFAULT_STYLE = 'label.style_label_down'
DEFAULT_COLOR = colors(4)['color.blue'][0]
DEFAULT_TEXTCOLOR = colors(4)['color.white'][0]
DEFAULT_SIZE = 'normal'
FIELDS = frozenset(('color', 'style', 'textcolor', 'size'))
SETTERS = {f'label.set_{field}': field for field in ('color', 'style', 'textcolor', 'size')}
OPTIONS = (('text', 'string'), ('xloc', 'string'), ('yloc', 'string'), ('color', 'color'),
           ('style', 'string'), ('textcolor', 'color'), ('size', 'string'))
OPERATIONS = {
    COORDINATE_CONSTRUCTOR: (('x', 'int'), ('y', 'float'), *OPTIONS),
    POINT_CONSTRUCTOR: (('point', 'chart.point'), *OPTIONS),
    'label.set_color': (('id', 'label'), ('color', 'color')),
    'label.set_style': (('id', 'label'), ('style', 'string')),
    'label.set_textcolor': (('id', 'label'), ('textcolor', 'color')),
    'label.set_size': (('id', 'label'), ('size', 'string')),
}


def constants(version):
    return {token: (token, 'string') for token in (*STYLES, *LOCATIONS)} if version >= 4 else {}


def apis(version):
    if version < 4:
        return []
    defaults = ['', TOKENS[0], PRICE, DEFAULT_COLOR, DEFAULT_STYLE, DEFAULT_TEXTCOLOR, DEFAULT_SIZE]
    optional = {**dict.fromkeys(CONSTRUCTORS, defaults), 'label.set_size': [DEFAULT_SIZE if version == 6 else 'auto']}
    return [
        dict(id=f'openpine.label-styles.v{version}.{op}', name='label.new' if op in CONSTRUCTORS else op, operation=op,
             parameters=[dict(name=name, types=[base, 'int'] if name == 'size' and version == 6 else [base],
                              qualifier='const' if name in ('xloc', 'yloc') else 'series')
                         for name, base in parameters],
             defaults=optional.get(op, []),
             named=True, result='label' if op in CONSTRUCTORS else 'void', effects=['draws_labels'])
        for op, parameters in OPERATIONS.items() if version >= 5 or op != POINT_CONSTRUCTOR
    ]


def contracts(version):
    return drawings.contracts(apis(version))


def evidence_version(reference, version):
    return dict(apis=apis(version), styles=list(STYLES) if version >= 4 else [],
                sizes=list(SIZES) if version >= 4 else [], reference_sha256=digest(reference))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/label-styles-profile.json')
    if (profile['schema_version'] != 'openpine.label-styles-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != evidence_version(snapshot.references[version], version)):
        raise ValueError('Label appearance ABI differs from pinned evidence.')


def validate_call(node, version):
    api = next(api for api in apis(version) if api['operation'] == node.operation)
    start = len(api['parameters']) - len(api['defaults'])
    for position, (argument, parameter) in enumerate(zip(node.arguments, api['parameters'], strict=True)):
        expression = argument.expression
        if parameter['name'] == 'xloc' and (type(expression) is not LiteralIR or expression.value not in TOKENS):
            raise ValueError('Expected xloc.bar_index or xloc.bar_time.')
        if parameter['name'] == 'yloc' and (type(expression) is not LiteralIR or expression.value != PRICE):
            raise ValueError('Label appearance requires yloc.price.')
        if argument.origin == 'default' and (position < start or type(expression) is not LiteralIR
                or expression.value_type != ValueType(parameter['types'][0])
                or expression.value != api['defaults'][position - start]):
            raise ValueError('Label appearance default differs from its closed signature.')


def valid_size(value):
    return value in SIZES or type(value) is int and 0 <= value < 2**63


def valid_appearance(record):
    return (record['style'] in STYLES.values() and valid_size(record['size'])
            and valid_color(record['color']) and valid_color(record['textcolor']))
