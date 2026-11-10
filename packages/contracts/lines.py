"""Pine lines in bar-index and price coordinates."""
from . import drawing_properties, drawings
from .chart_points import for_drawing
from .drawing_clock import chart_x
from .line_extensions import MODES
from .line_styles import FIELDS as STYLE_FIELDS, OPERATIONS as STYLE_OPERATIONS, valid_stroke
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.25'
STATE_SCHEMA = 'openpine.runtime-state.17'
OPERATIONS = {
    'line.new': (('x1', 'int'), ('y1', 'float'), ('x2', 'int'), ('y2', 'float')),
    'line.set_xy1': (('id', 'line'), ('x', 'int'), ('y', 'float')),
    'line.set_xy2': (('id', 'line'), ('x', 'int'), ('y', 'float')),
    'line.delete': (('id', 'line'),),
}
MUTATIONS = (*OPERATIONS, 'line.new_extended', 'line.set_extend', 'line.new_xloc', 'line.set_xloc',
             *for_drawing('line'), *STYLE_OPERATIONS, *drawing_properties.for_kind('line', drawing_properties.SETTERS))
ALL_OPERATIONS = (*MUTATIONS, 'line.get_price', 'line.get_x1', 'line.get_x2',
                  *drawing_properties.for_kind('line', drawing_properties.GETTERS))


def apis(version):
    return drawings.apis(version, 'line', OPERATIONS, [])


def contracts(version):
    return drawings.contracts(apis(version))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/lines-profile.json')
    if (profile['schema_version'] != 'openpine.lines-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != dict(apis=apis(version), reference_sha256=digest(snapshot.references[version]))):
        raise ValueError('Line ABI differs from pinned evidence.')


def uses_lines(value, *, calls_only=False):
    return drawings.uses_drawing(value, 'line', MUTATIONS if calls_only else ALL_OPERATIONS, calls_only)


def validate_line(value):
    from .drawing_xloc import valid_xloc
    if type(value) is not dict:
        raise ValueError('Invalid line record.')
    geometry = {'id', 'x1', 'y1', 'x2', 'y2'}
    located = geometry | {'extend', 'xloc'}
    styled = located | STYLE_FIELDS
    keys = value.keys() - {'force_overlay'}
    if (keys not in (geometry, geometry | {'extend'}, located, styled)
            or 'force_overlay' in value and (value['force_overlay'] is not True or keys != styled)
            or keys == styled and not valid_stroke(value)
            or not valid_xloc(value)
            or not drawings.valid_id(value['id'])
            or not drawings.valid_index(value['x1']) or not drawings.valid_index(value['x2'])
            or not drawings.valid_price(value['y1']) or not drawings.valid_price(value['y2'])
            or value.get('extend', 'none') not in MODES):
        raise ValueError('Invalid line record.')


def chart_lines(lines, start, end):
    if end <= start:
        return []
    result = []
    for line in lines:
        x1, x2 = chart_x(line, 'x1'), chart_x(line, 'x2')
        y1, y2 = line['y1'], line['y2']
        if None in (x1, y1, x2, y2) or (x1, y1) == (x2, y2):
            continue
        mode = line.get('extend', 'none') if x1 != x2 else 'none'
        reaches_end = min(x1, x2) < end or mode in ('left', 'both')
        reaches_start = max(x1, x2) >= start or mode in ('right', 'both')
        if reaches_end and reaches_start:
            result.append(line)
    return result
