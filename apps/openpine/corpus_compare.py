"""Exact structural comparisons and a bounded first-difference explanation."""
from dataclasses import asdict, fields, is_dataclass
import struct

from packages.contracts.wire import canonical, digest


def tagged(value):
    # Python equality aliases bool/int and int/float; qualification must not.
    if isinstance(value, dict):
        return ['object', [[k, tagged(v)] for k, v in sorted(value.items())]]
    if isinstance(value, (list, tuple)):
        return ['array', [tagged(v) for v in value]]
    if type(value) is float:
        return ['float64', struct.pack('>d', value).hex()]
    return [type(value).__name__, value]


def locations(program):
    result = []
    def walk(node):
        if is_dataclass(node):
            operation = getattr(node, 'operation', '')
            if operation and getattr(node, 'span', None):
                result.append({'operation': operation, 'call_site_id': getattr(node, 'call_site_id', None),
                               'span': asdict(node.span)})
            for field in fields(node):
                if field.name not in ('identity', 'language_policy', 'span'):
                    walk(getattr(node, field.name))
        elif isinstance(node, (tuple, list)):
            for child in node:walk(child)
    walk(program)
    return result[:128]


def compare(name, left, right, *, source_locations=(), context_id=None):
    left_hash, right_hash = digest(tagged(left)), digest(tagged(right))
    result = {'name': name, 'status': 'passed' if left_hash == right_hash else 'mismatch',
              'left_sha256': left_hash, 'right_sha256': right_hash}
    if left_hash == right_hash:return result
    path = []
    a, b = left, right
    while True:
        if isinstance(a, dict) and isinstance(b, dict) and a.keys() == b.keys():
            key = next((k for k in sorted(a) if tagged(a[k]) != tagged(b[k])), None)
            if key is None:break
        elif isinstance(a, (tuple, list)) and isinstance(b, (tuple, list)):
            key = next((i for i in range(min(len(a), len(b))) if tagged(a[i]) != tagged(b[i])), None)
            if key is None:
                if len(a)!=len(b):
                    key=min(len(a),len(b));path.append(key)
                    a,b=(a[key] if key<len(a) else {'missing':True}),(b[key] if key<len(b) else {'missing':True})
                break
        else:break
        path.append(key);a, b = a[key], b[key]
    def small(value):
        raw = canonical(value)
        return value if len(raw) <= 4096 else {'sha256': digest(tagged(value)), 'bytes': len(raw), 'omitted': True}
    window = {}
    # Prefer the series/ledger row index to an inner field/column index.
    parent_a, parent_b = left, right
    for key in path:
        if isinstance(key, int) and isinstance(parent_a, (tuple, list)):
            window = {'start': max(0, key - 2), 'left': small(parent_a[max(0,key-2):key+3]),
                      'right': small(parent_b[max(0,key-2):key+3])}
            break
        parent_a, parent_b = parent_a[key], parent_b[key]
    position = {}
    if len(path) >= 2 and path[0] in ('rows','events') and isinstance(path[1], int):
        index = path[1]
        row = left[path[0]][index] if index < len(left[path[0]]) else right[path[0]][index]
        if path[0]=='rows' and len(row)==4:
            position = {'bar_index':row[0], 'time':row[1], 'output_id':row[2]}
        elif isinstance(row,dict):
            position = {key:row[key] for key in ('sequence','bar_index','time','kind') if key in row}
    relevant = [loc for loc in source_locations if loc.get('call_site_id') and loc['call_site_id'] in str(position.get('output_id',''))]
    result['first_difference'] = {'path': path, 'left': small(a), 'right': small(b),
        'context_id': context_id, **position, 'window': window,
        'source_locations': (relevant or list(source_locations))[:16],
        'location_scope': 'Relevant original call sites; no per-expression execution trace.'}
    return result


def historical(response, bars):
    value = response['result']
    rows = []
    for index, bar in enumerate(bars):
        for output in value['outputs']:
            rows.append([index, bar['time'], output['output_id'], output['values'][index]])
    simulation = value.get('simulation') or {}
    return {'rows': rows, 'events': simulation.get('events', []), 'statistics': simulation.get('statistics')}
