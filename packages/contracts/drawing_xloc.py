"""Explicit coordinate modes and additive constructor contracts."""
from . import drawings
from .colors import constants as colors
from .model import LiteralIR, ValueType
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.29'
STATE_SCHEMA = 'openpine.runtime-state.21'
MODES = ('bar_index', 'bar_time')
TOKENS = tuple('xloc.' + mode for mode in MODES)
OPERATIONS = {
    'line.new_xloc': (('x1', 'int'), ('y1', 'float'), ('x2', 'int'), ('y2', 'float'),
                      ('xloc', 'string'), ('extend', 'string')),
    'label.new_xloc': (('x', 'int'), ('y', 'float'), ('text', 'string'), ('xloc', 'string')),
    'box.new_xloc': (('left', 'int'), ('top', 'float'), ('right', 'int'), ('bottom', 'float'),
                     ('border_color', 'color'), ('bgcolor', 'color'), ('xloc', 'string')),
}
POSITIONAL = {'line.new_xloc': 4, 'label.new_xloc': 3, 'box.new_xloc': 4}


def constants(version):
    return {token: (token, 'string') for token in TOKENS} if version >= 4 else {}


def apis(version):
    if version < 4:
        return []
    blue = colors(version)['color.blue'][0]
    defaults = ([TOKENS[0], 'extend.none'], ['', TOKENS[0]], [blue, blue, TOKENS[0]])
    result = []
    for (op, parameters), values in zip(OPERATIONS.items(), defaults, strict=True):
        kind, _ = op.split('.')
        plural = drawings.PLURALS[kind]
        result.append(dict(id=f'openpine.drawing-xloc.v{version}.{op}', name=kind + '.new', operation=op,
            parameters=[dict(name=n, types=[t], qualifier='const' if n == 'xloc' else 'series') for n, t in parameters],
            defaults=values, named=True, result=kind, effects=[f'draws_{plural}']))
    return result


def contracts(version):
    return drawings.contracts(apis(version))


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/drawing-xloc-profile.json')
    expected = dict(apis=apis(version), constants=list(constants(version)),
                    reference_sha256=digest(snapshot.references[version]))
    if (profile['schema_version'] != 'openpine.drawing-xloc-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != expected):
        raise ValueError('Drawing coordinate ABI differs from pinned evidence.')


def uses_xloc(value):
    from .drawing_xloc_mutations import OPERATIONS as mutations_and_reads
    return drawings.uses_drawing(value, '', (*OPERATIONS, *mutations_and_reads), True)


def validate_call(node, version):
    api = next(api for api in apis(version) if api['operation'] == node.operation)
    first_optional = len(api['parameters']) - len(api['defaults'])
    for i, (arg, parameter) in enumerate(zip(node.arguments, api['parameters'], strict=True)):
        expression = arg.expression
        if parameter['name'] == 'xloc' and (
                arg.origin != 'source' or type(expression) is not LiteralIR or expression.value not in TOKENS):
            raise ValueError('Expected an explicit xloc.bar_index or xloc.bar_time.')
        if arg.origin == 'default' and (
                i < first_optional or type(expression) is not LiteralIR
                or expression.value_type != ValueType(parameter['types'][0])
                or expression.value != api['defaults'][i - first_optional]):
            raise ValueError('Drawing coordinate constructor default differs from its closed signature.')


def valid_xloc(record):
    return 'xloc' not in record or record['xloc'] in MODES
