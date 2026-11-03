"""Additive ABI for line extension and reading prices by bar index."""
from .drawings import contracts as drawing_contracts
from .wire import digest, read_json

MODES = ('none', 'left', 'right', 'both')
OPERATIONS = {
    'line.new_extended': (('x1', 'int'), ('y1', 'float'), ('x2', 'int'), ('y2', 'float'), ('extend', 'string')),
    'line.set_extend': (('id', 'line'), ('extend', 'string')),
    'line.get_price': (('id', 'line'), ('x', 'int')),
}
NAMES = ('line.new', 'line.set_extend', 'line.get_price')


def effects(operation):
    return ('reads_lines',) if operation == 'line.get_price' else ('draws_lines',)


def apis(version):
    if version < 4:
        return []
    return [
        dict(id=f'openpine.line-extensions.v{version}.{op}', name=name, operation=op,
             parameters=[dict(name=n, types=[t], qualifier='series') for n, t in OPERATIONS[op]],
             defaults=[], named=True, effects=list(effects(op)), result=result)
        for (name, op, result) in zip(NAMES, OPERATIONS, ('line', 'void', 'float'), strict=True)
    ]


def contracts(version):
    return drawing_contracts(apis(version))


def constants(version):
    return {f'extend.{mode}': (f'extend.{mode}', 'string') for mode in MODES} if version >= 4 else {}


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/line-extensions-profile.json')
    if (profile['schema_version'] != 'openpine.line-extensions-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != dict(apis=apis(version), constants=list(constants(version)),
                                                        reference_sha256=digest(snapshot.references[version]))):
        raise ValueError('Line extension ABI differs from pinned evidence.')
