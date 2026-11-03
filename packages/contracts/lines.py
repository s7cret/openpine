"""Finite Pine line segments in bar-index and price coordinates."""
from . import drawings
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.25'
STATE_SCHEMA = 'openpine.runtime-state.17'
OPERATIONS = {
    'line.new': (('x1', 'int'), ('y1', 'float'), ('x2', 'int'), ('y2', 'float')),
    'line.set_xy1': (('id', 'line'), ('x', 'int'), ('y', 'float')),
    'line.set_xy2': (('id', 'line'), ('x', 'int'), ('y', 'float')),
    'line.delete': (('id', 'line'),),
}


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
    return drawings.uses_drawing(value, 'line', OPERATIONS, calls_only)


def validate_line(value):
    if (type(value) is not dict or value.keys() != {'id', 'x1', 'y1', 'x2', 'y2'}
            or not drawings.valid_id(value['id'])
            or not drawings.valid_index(value['x1']) or not drawings.valid_index(value['x2'])
            or not drawings.valid_price(value['y1']) or not drawings.valid_price(value['y2'])):
        raise ValueError('Invalid line record.')


def chart_lines(lines, start, end):
    if end <= start:
        return []
    result = []
    for line in lines:
        x1, y1, x2, y2 = (line[key] for key in ('x1', 'y1', 'x2', 'y2'))
        if None in (x1, y1, x2, y2) or (x1, y1) == (x2, y2):
            continue
        if min(x1, x2) < end and max(x1, x2) >= start:
            result.append(line)
    return result
