"""Versioned single-field access to the existing Pine drawing records."""

from dataclasses import replace

from .drawings import PLURALS, contracts
from .wire import digest

LABEL_TEXT = 'label.get_text'
GETTERS = {
    'line.get_y1': ('y1', 'float'),
    'line.get_y2': ('y2', 'float'),
    'label.get_y': ('y', 'float'),
    LABEL_TEXT: ('text', 'string'),
    'box.get_top': ('top', 'float'),
    'box.get_bottom': ('bottom', 'float'),
}
SETTERS = {
    'line.set_x1': ('x', 'x1', 'int'),
    'line.set_x2': ('x', 'x2', 'int'),
    'line.set_y1': ('y', 'y1', 'float'),
    'line.set_y2': ('y', 'y2', 'float'),
    'label.set_x': ('x', 'x', 'int'),
    'box.set_left': ('left', 'left', 'int'),
    'box.set_right': ('right', 'right', 'int'),
    'box.set_top': ('top', 'top', 'float'),
    'box.set_bottom': ('bottom', 'bottom', 'float'),
}
OPERATIONS = {
    **{op: (('id', op.partition('.')[0]),) for op in GETTERS},
    **{op: (('id', op.partition('.')[0]), (name, base)) for op, (name, _, base) in SETTERS.items()},
}


def for_kind(kind, operations):
    return tuple(op for op in operations if op.startswith(kind + '.'))


def effects(operation):
    plural = PLURALS[operation.partition('.')[0]]
    return (('reads_' if operation in GETTERS else 'draws_') + plural,)


def source_type(spelling):
    base = spelling[7:-1] if spelling.startswith('series[') else spelling.split()[-1]
    return 'int' if base == 'integer' else base


def property_contract(row):
    name = row['name']
    version = row['language_version']
    parameters = OPERATIONS[name]
    result = GETTERS[name][1] if name in GETTERS else 'void'
    if (tuple(p['name'] for p in row['parameters']) != tuple(p for p, _ in parameters)
            or [source_type(t) for t in row['result_type_spellings']] != [result]):
        raise ValueError(f'Unexpected pinned signature for {name}.')
    for parameter, (_, base) in zip(row['parameters'], parameters, strict=True):
        spellings = parameter['type_spellings']
        if base not in {source_type(t) for t in spellings} and not (version == 4 and spellings == ['series']):
            raise ValueError(f'Unexpected pinned parameter type for {name}.')
        if parameter['requiredness_claim'] not in (('required', 'unknown') if version == 4 else ('required',)):
            raise ValueError(f'Unexpected pinned parameter arity for {name}.')
    api = dict(id=f'openpine.drawing-properties.v{version}.{name}', name=name, operation=name,
               parameters=[dict(name=p, types=[base], qualifier='series') for p, base in parameters],
               defaults=[], named=True, result=result, effects=list(effects(name)))
    contract = contracts((api,))[api['id']]
    return api, replace(contract, signature_sha256=digest({'api': api, 'reference': row}))
