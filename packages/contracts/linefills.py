"""Bounded Pine fills whose geometry is owned by two live lines."""
from . import drawings
from .colors import valid as valid_color
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.28'
STATE_SCHEMA = 'openpine.runtime-state.20'
OPERATIONS = {
    'linefill.new': (('line1', 'line'), ('line2', 'line'), ('color', 'color')),
    'linefill.set_color': (('id', 'linefill'), ('color', 'color')),
    'linefill.delete': (('id', 'linefill'),),
    'linefill.get_line1': (('id', 'linefill'),),
    'linefill.get_line2': (('id', 'linefill'),),
}
READS = {'linefill.get_line1': 'line1', 'linefill.get_line2': 'line2'}
MUTATIONS = tuple(op for op in OPERATIONS if op not in READS)


def effects(operation):
    return ('reads_linefills',) if operation in READS else ('draws_linefills',)


def apis(version):
    if version < 5:
        return []
    entries = drawings.apis(version, 'linefill', OPERATIONS, [])
    for entry in entries:
        if entry['operation'] in READS:
            entry.update(result='line', effects=['reads_linefills'])
    return entries


def contracts(version):
    return drawings.contracts(apis(version))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/linefills-profile.json')
    expected = dict(apis=apis(version), reference_sha256=digest(snapshot.references[version]))
    if (profile['schema_version'] != 'openpine.linefills-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != expected):
        raise ValueError('Linefill ABI differs from pinned evidence.')


def uses_linefills(value, *, calls_only=False):
    return drawings.uses_drawing(value, 'linefill', MUTATIONS if calls_only else OPERATIONS, calls_only)


def pair(line1, line2):
    return min(line1, line2), max(line1, line2)


def validate_snapshot(values, next_id, lines):
    if type(values) is not list or len(values) > drawings.LIMIT:
        raise ValueError('Invalid live linefill snapshot.')
    endpoints = {line['id'] for line in lines}
    previous, pairs = 0, set()
    for fill in values:
        if (type(fill) is not dict or fill.keys() != {'id', 'line1', 'line2', 'color'}
                or not drawings.valid_id(fill['id']) or not previous < fill['id'] < next_id
                or not drawings.valid_id(fill['line1']) or not drawings.valid_id(fill['line2'])
                or fill['line1'] not in endpoints or fill['line2'] not in endpoints
                or fill['line1'] == fill['line2'] or not valid_color(fill['color'])):
            raise ValueError('Invalid linefill record or missing boundary.')
        key = pair(fill['line1'], fill['line2'])
        if key in pairs:
            raise ValueError('Repeated linefill pair.')
        pairs.add(key)
        previous = fill['id']


def chart_linefills(fills, visible_lines):
    endpoints = {line['id'] for line in visible_lines}
    return [fill for fill in fills if fill['line1'] in endpoints and fill['line2'] in endpoints]
