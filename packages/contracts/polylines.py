"""Straight polyline ABI, geometry bounds and chart window selection."""
from . import drawings
from .chart_points import POINT_ARRAY, POINT_ARRAY_OPS
from .colors import constants, valid as valid_color
from .drawing_xloc import MODES, TOKENS
from .model import LiteralIR, NAIR, ValueType
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.31'
STATE_SCHEMA = 'openpine.runtime-state.23'
MAX_VERTICES = 512
MAX_LIVE_VERTICES = 8192
MAX_BAR_VERTICES = 8192
EVENT_PAGE = 100
CONSTRUCTOR = 'polyline.new'
OVERLAY_CONSTRUCTOR = 'polyline.new_overlay'
CONSTRUCTORS = (CONSTRUCTOR, OVERLAY_CONSTRUCTOR)
BASE_OPERATIONS = {
    CONSTRUCTOR: (('points', POINT_ARRAY), ('curved', 'bool'), ('closed', 'bool'), ('xloc', 'string'),
                  ('line_color', 'color'), ('fill_color', 'color')),
    'polyline.delete': (('id', 'polyline'),),
}
OPERATIONS = {**BASE_OPERATIONS, OVERLAY_CONSTRUCTOR: (*BASE_OPERATIONS[CONSTRUCTOR], ('force_overlay', 'bool'))}


def apis(version):
    if version < 5:
        return []
    result = drawings.apis(version, 'polyline', BASE_OPERATIONS,
                           [False, False, TOKENS[0], constants(version)['color.blue'][0], None])
    constructor = result[0]
    for param in constructor['parameters']:
        if param['name'] in ('curved', 'xloc'):
            param['qualifier'] = 'const'
    result.append(dict(constructor, id=f'openpine.polylines.v{version}.{OVERLAY_CONSTRUCTOR}',
                       operation=OVERLAY_CONSTRUCTOR,
                       parameters=[*constructor['parameters'], dict(name='force_overlay', types=['bool'], qualifier='const')],
                       defaults=[*constructor['defaults'], False]))
    return result


def contracts(version):
    return drawings.contracts(apis(version))


def uses_polylines(value, *, calls_only=False):
    return drawings.uses_drawing(value, 'polyline', OPERATIONS, calls_only)


def policy():
    return dict(vertices=MAX_VERTICES, live_vertices=MAX_LIVE_VERTICES, vertices_per_bar=MAX_BAR_VERTICES,
                live_drawings=drawings.LIMIT, events_page=EVENT_PAGE,
                complete_coordinates=True, minimum_vertices={'open': 2, 'closed': 3},
                point_array_operations=sorted(POINT_ARRAY_OPS))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/polylines-profile.json')
    expected = dict(apis=apis(version), reference_sha256=digest(snapshot.references[version]))
    if (profile['schema_version'] != 'openpine.polylines-spec.1' or profile['policy'] != policy()
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != expected):
        raise ValueError('Polyline ABI differs from pinned evidence.')


def validate_call(node, version):
    api = next(api for api in apis(version) if api['operation'] == node.operation)
    optional = len(api['parameters']) - len(api['defaults'])
    for i, (arg, param) in enumerate(zip(node.arguments, api['parameters'], strict=True)):
        value = arg.expression
        if param['name'] == 'curved' and (type(value) is not LiteralIR or value.value is not False):
            raise ValueError('Polyline segments require curved=false.')
        if param['name'] == 'xloc' and (type(value) is not LiteralIR or value.value not in TOKENS):
            raise ValueError('Expected xloc.bar_index or xloc.bar_time.')
        if arg.origin != 'default':
            continue
        if i < optional:
            raise ValueError('A required polyline argument has no default.')
        expected, typ = api['defaults'][i - optional], ValueType(param['types'][0])
        if value.value_type != typ or (type(value) is not NAIR if expected is None else
                type(value) is not LiteralIR or value.value != expected):
            raise ValueError('Polyline default differs from its closed signature.')


def validate_polyline(record):
    if (type(record) is not dict
            or record.keys() - {'force_overlay'} != {'id', 'vertices', 'closed', 'xloc', 'line_color', 'fill_color'}
            or 'force_overlay' in record and record['force_overlay'] is not True
            or not drawings.valid_id(record['id']) or type(record['closed']) is not bool
            or record['xloc'] not in MODES or not valid_color(record['line_color']) or not valid_color(record['fill_color'])):
        raise ValueError('Invalid polyline record.')
    vertices = record['vertices']
    if (type(vertices) not in (tuple, list) or not (3 if record['closed'] else 2) <= len(vertices) <= MAX_VERTICES
            or any(type(p) not in (tuple, list) or len(p) != 2 or None in p
                   or not drawings.valid_index(p[0]) or not drawings.valid_price(p[1]) for p in vertices)):
        raise ValueError('Invalid polyline vertices.')


def chart_polylines(records, start, end):
    if end <= start:
        return []
    visible = []
    for record in records:
        vertices = record.get('chart_vertices', ()) if record['xloc'] == 'bar_time' else record['vertices']
        xs = [x for x, _ in vertices if x is not None]
        if xs and min(xs) <= end - 1 and max(xs) >= start:
            visible.append(record)
    return visible
