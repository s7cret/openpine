"""Mode changes and raw coordinate reads for existing drawings."""
from . import drawings
from .drawing_xloc import TOKENS
from .model import LiteralIR
from .wire import digest, read_json

SETTERS = {
    'line.set_xloc': ('x1', 'x2'),
    'label.set_xloc': ('x',),
    'box.set_xloc': ('left', 'right'),
}
GETTERS = {
    'line.get_x1': 'x1',
    'line.get_x2': 'x2',
    'label.get_x': 'x',
    'box.get_left': 'left',
    'box.get_right': 'right',
}
OPERATIONS = {
    **{op: (('id', op.split('.')[0]), *((key, 'int') for key in keys), ('xloc', 'string'))
       for op, keys in SETTERS.items()},
    **{op: (('id', op.split('.')[0]),) for op in GETTERS},
}


def effects(operation):
    plural = drawings.PLURALS[operation.split('.')[0]]
    return (f'draws_{plural}',) if operation in SETTERS else (f'reads_{plural}',)


def apis(version):
    if version < 4:
        return []
    return [
        dict(id=f'openpine.drawing-xloc-mutations.v{version}.{op}', name=op, operation=op,
             parameters=[dict(name=name, types=[base], qualifier='const' if name == 'xloc' else 'series')
                         for name, base in parameters],
             defaults=[], named=True, result='void' if op in SETTERS else 'int', effects=list(effects(op)))
        for op, parameters in OPERATIONS.items() if version >= 6 or op != 'box.set_xloc'
    ]


def contracts(version):
    return drawings.contracts(apis(version))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/drawing-xloc-mutations-profile.json')
    expected = dict(apis=apis(version), reference_sha256=digest(snapshot.references[version]))
    if (profile['schema_version'] != 'openpine.drawing-xloc-mutations-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != expected):
        raise ValueError('Drawing coordinate mutation and read ABI differs from pinned evidence.')


def validate_call(node):
    if any(arg.origin != 'source' for arg in node.arguments):
        raise ValueError('All drawing coordinate arguments must be supplied by the source.')
    if node.operation in SETTERS:
        mode = node.arguments[-1].expression
        if type(mode) is not LiteralIR or mode.value not in TOKENS:
            raise ValueError('Expected an explicit xloc.bar_index or xloc.bar_time.')
