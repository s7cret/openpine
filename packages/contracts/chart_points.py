"""Closed chart.point and point-based drawing contracts for Pine v5/v6."""
from dataclasses import fields, is_dataclass

from . import drawings
from .box_borders import POINT_CONSTRUCTOR as STYLED_POINT_BOX
from .box_text import POINT_CONSTRUCTOR as TEXT_POINT_BOX
from .colors import constants
from .drawing_xloc import TOKENS
from .label_styles import POINT_CONSTRUCTOR as STYLED_POINT_LABEL
from .label_text import POINT_CONSTRUCTOR as TEXT_POINT_LABEL
from .label_yloc import POINT_CONSTRUCTOR as VERTICAL_POINT_LABEL
from .line_styles import POINT_CONSTRUCTOR as STYLED_POINT_LINE
from .model import LiteralIR, NAIR, ObjectFieldContract, ObjectTypeContract, ParameterContract, ReferenceIR, SourceSpan, ValueType
from .wire import digest, read_json

BASE = 'chart.point'
POINT_ARRAY = 'array.' + BASE
POINT_ARRAY_OPS = frozenset(('new', 'from', 'push', 'get', 'set', 'clear', 'size', 'is_na'))
IR_SCHEMA = 'openpine.typed-ir.30'
STATE_SCHEMA = 'openpine.runtime-state.22'
FIELDS = (('time', 'int'), ('index', 'int'), ('price', 'float'))
PRICE = 2
_BUILTIN = SourceSpan('<chart.point>', 0, 0, 1, 1, 1, 1)
LAYOUT = ObjectTypeContract(BASE, BASE, tuple(
    ObjectFieldContract(name, kind, False, NAIR(ValueType(kind), _BUILTIN), _BUILTIN)
    for name, kind in FIELDS), _BUILTIN)

CONSTRUCTORS = {
    'chart.point.new': FIELDS,
    'chart.point.now': (FIELDS[PRICE],),
    'chart.point.from_index': FIELDS[1:],
    'chart.point.from_time': (FIELDS[0], FIELDS[PRICE]),
    'chart.point.copy': (('id', BASE),),
}
DRAWING_CONSTRUCTORS = {
    'line.new_points': (('first_point', BASE), ('second_point', BASE), ('xloc', 'string'), ('extend', 'string')),
    'label.new_points': (('point', BASE), ('text', 'string'), ('xloc', 'string')),
    'box.new_points': (('top_left', BASE), ('bottom_right', BASE), ('border_color', 'color'),
                       ('xloc', 'string'), ('bgcolor', 'color')),
}
POINT_SETTERS = {
    'line.set_first_point': 'line.set_xy1',
    'line.set_second_point': 'line.set_xy2',
    'label.set_point': 'label.set_xy',
    'box.set_top_left_point': 'box.set_lefttop',
    'box.set_bottom_right_point': 'box.set_rightbottom',
}
OPERATIONS = {**CONSTRUCTORS, **DRAWING_CONSTRUCTORS, **{
    op: (('id', op.split('.')[0]), ('point', BASE)) for op in POINT_SETTERS}}


def for_drawing(kind):
    return tuple(op for op in (*DRAWING_CONSTRUCTORS, *POINT_SETTERS) if op.startswith(kind + '.'))


def apis(version):
    if version < 5:
        return []
    blue = constants(version)['color.blue'][0]
    defaults = {
        'chart.point.now': [{'reference': 'close'}],
        'line.new_points': [TOKENS[0], 'extend.none'],
        'label.new_points': ['', TOKENS[0]],
        'box.new_points': [blue, TOKENS[0], blue],
    }
    result = []
    for op, parameters in OPERATIONS.items():
        kind = op.split('.')[0]
        effects = [] if op in CONSTRUCTORS else [f'draws_{drawings.PLURALS[kind]}']
        result.append(dict(id=f'openpine.chart-points.v{version}.{op}',
            name=op.removesuffix('_points'), operation=op,
            parameters=[dict(name=name, types=[base], qualifier='const' if name == 'xloc' else 'series')
                        for name, base in parameters], defaults=defaults.get(op, []), named=True,
            result=BASE if op in CONSTRUCTORS else kind if op in DRAWING_CONSTRUCTORS else 'void',
            effects=effects))
    return result


def contracts(version):
    return drawings.contracts(apis(version))


def uses_points(value):
    operation = getattr(value, 'operation', None) or getattr(getattr(value, 'contract', None), 'operation', None)
    if operation in OPERATIONS or operation in (
            STYLED_POINT_LINE, STYLED_POINT_BOX, TEXT_POINT_BOX, STYLED_POINT_LABEL, VERTICAL_POINT_LABEL, TEXT_POINT_LABEL):
        return True
    if isinstance(value, ValueType):
        return value.base in (BASE, POINT_ARRAY)
    if isinstance(value, ParameterContract) and value.annotation in (BASE, POINT_ARRAY):
        return True
    if type(value) is tuple:
        return any(map(uses_points, value))
    return is_dataclass(value) and any(uses_points(getattr(value, field.name)) for field in fields(value))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/chart-points-profile.json')
    expected = dict(apis=apis(version), fields=[list(field) for field in FIELDS] if version >= 5 else [],
                    reference_sha256=digest(snapshot.references[version]))
    if (profile['schema_version'] != 'openpine.chart-points-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != expected):
        raise ValueError('Chart point ABI differs from pinned evidence.')


def validate_call(node, version):
    api = next(api for api in apis(version) if api['operation'] == node.operation)
    first_optional = len(api['parameters']) - len(api['defaults'])
    for i, (arg, parameter) in enumerate(zip(node.arguments, api['parameters'], strict=True)):
        expression = arg.expression
        if parameter['name'] == 'xloc' and (type(expression) is not LiteralIR or expression.value not in TOKENS):
            raise ValueError('Expected xloc.bar_index or xloc.bar_time.')
        if arg.origin != 'default':
            continue
        if i < first_optional:
            raise ValueError('A required chart point argument has no default.')
        expected = api['defaults'][i - first_optional]
        if type(expected) is dict:
            matches = type(expression) is ReferenceIR and expression.contract.slot == expected['reference']
        else:
            matches = (type(expression) is LiteralIR and expression.value_type == ValueType(parameter['types'][0])
                       and expression.value == expected)
        if not matches:
            raise ValueError('Chart point call default differs from its closed signature.')
