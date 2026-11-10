"""Closed Pine table ABI and bounded pane snapshots."""
from dataclasses import replace

from . import drawings
from .colors import constants as color_constants, valid as valid_color
from .drawing_properties import source_type
from .model import LiteralIR, NAIR, ValueType
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.27'
STATE_SCHEMA = 'openpine.runtime-state.19'
CELL_LIMIT = 256
TEXT_LIMIT = 512
MAX_BORDER_WIDTH = 64
DEFAULT_TEXT_COLOR = color_constants(4)['color.black'][0]
POSITIONS = tuple(f'{y}_{x}' for y in ('top', 'middle', 'bottom') for x in ('left', 'center', 'right'))
CONSTRUCTOR = 'table.new'
OVERLAY_CONSTRUCTOR = 'table.new_overlay'
STYLED_CONSTRUCTOR = 'table.new_styled'
STYLED_OVERLAY_CONSTRUCTOR = 'table.new_styled_overlay'
STYLED_CONSTRUCTORS = (STYLED_CONSTRUCTOR, STYLED_OVERLAY_CONSTRUCTOR)
STYLE_DEFAULTS = {'frame_color': None, 'frame_width': 0, 'border_color': None, 'border_width': 0}
STYLE_PARAMETERS = (('frame_color', 'color'), ('frame_width', 'int'),
                    ('border_color', 'color'), ('border_width', 'int'))
TABLE_SETTERS = {'table.set_position': ('position', 'string'), 'table.set_bgcolor': ('bgcolor', 'color'),
                 **{'table.set_' + field: (field, base) for field, base in STYLE_PARAMETERS}}
CELL_SETTERS = {'table.cell_set_text': ('text', 'string'), 'table.cell_set_text_color': ('text_color', 'color'),
                'table.cell_set_bgcolor': ('bgcolor', 'color')}
BASE_OPERATIONS = {
    CONSTRUCTOR: (('position', 'string'), ('columns', 'int'), ('rows', 'int'), ('bgcolor', 'color')),
    'table.cell': (('table_id', 'table'), ('column', 'int'), ('row', 'int'), ('text', 'string'),
                   ('text_color', 'color'), ('bgcolor', 'color')),
    'table.clear': (('table_id', 'table'), ('start_column', 'int'), ('start_row', 'int'),
                    ('end_column', 'int'), ('end_row', 'int')),
    'table.delete': (('table_id', 'table'),),
}
OPERATIONS = {
    **BASE_OPERATIONS,
    OVERLAY_CONSTRUCTOR: (*BASE_OPERATIONS[CONSTRUCTOR], ('force_overlay', 'bool')),
    STYLED_CONSTRUCTOR: (*BASE_OPERATIONS[CONSTRUCTOR], *STYLE_PARAMETERS),
    STYLED_OVERLAY_CONSTRUCTOR: (*BASE_OPERATIONS[CONSTRUCTOR], *STYLE_PARAMETERS, ('force_overlay', 'bool')),
    **{op: (('table_id', 'table'), prop) for op, prop in TABLE_SETTERS.items()},
    **{op: (('table_id', 'table'), ('column', 'int'), ('row', 'int'), prop) for op, prop in CELL_SETTERS.items()},
}


def constants(version):
    return {f'position.{value}': (value, 'string') for value in POSITIONS} if version >= 4 else {}


def apis(version):
    result = drawings.apis(version, 'table', BASE_OPERATIONS, [None])
    if result:
        result[1]['defaults'] = ['', DEFAULT_TEXT_COLOR, None]
        result[2]['defaults'] = [None, None]
    if version >= 5:
        base = result[0]
        result.append(dict(base, id=f'openpine.tables.v{version}.{OVERLAY_CONSTRUCTOR}', operation=OVERLAY_CONSTRUCTOR,
                           parameters=[*base['parameters'], dict(name='force_overlay', types=['bool'], qualifier='const')],
                           defaults=[*base['defaults'], False]))
    return result


def contracts(version):
    return drawings.contracts(apis(version))


def style_apis(version):
    if version < 4:
        return []
    constructors = STYLED_CONSTRUCTORS if version >= 5 else (STYLED_CONSTRUCTOR,)
    result = []
    for operation in (*constructors, *TABLE_SETTERS, *CELL_SETTERS):
        if operation in constructors:
            defaults = [None, *STYLE_DEFAULTS.values()]
            if operation == STYLED_OVERLAY_CONSTRUCTOR:
                defaults.append(False)
        elif operation in TABLE_SETTERS and operation != 'table.set_position':
            defaults = [STYLE_DEFAULTS.get(TABLE_SETTERS[operation][0])]
        else:
            defaults = []
        result.append(dict(
            id=f'openpine.table-style.v{version}.{operation}', operation=operation,
            name=CONSTRUCTOR if operation in constructors else operation,
            parameters=[dict(name=name, types=[base], qualifier='const' if name == 'force_overlay' else 'series')
                        for name, base in OPERATIONS[operation]],
            defaults=defaults, named=True, result='table' if operation in constructors else 'void',
            effects=['draws_tables'],
        ))
    return result


def style_contracts(snapshot, version):
    additions = style_apis(version)
    if not additions:
        return ()
    names = {api['name'] for api in additions}
    rows = {row['name']: row for row in snapshot.references[version]['entries']
            if row['category'] == 'functions' and row['name'] in names}
    registered = drawings.contracts(additions)
    result = []
    for api in additions:
        row = rows.get(api['name'])
        names = tuple(p['name'] for p in api['parameters'])
        if row is None or [source_type(s) for s in row['result_type_spellings']] != [api['result']]:
            raise ValueError(f'Missing pinned table signature for {api["name"]}.')
        pinned_names = tuple(p['name'] for p in row['parameters'])
        expected = (*names, 'force_overlay') if api['operation'] == STYLED_CONSTRUCTOR and version >= 5 else names
        if pinned_names != expected:
            raise ValueError(f'Unexpected pinned parameter order for {api["name"]}.')
        for parameter, declared in zip(row['parameters'][:len(names)], api['parameters'], strict=True):
            if declared['types'][0] not in {source_type(s) for s in parameter['type_spellings']}:
                raise ValueError(f'Unexpected pinned parameter type for {api["name"]}.')
        contract = replace(registered[api['id']], signature_sha256=digest({'api': api, 'reference': row}))
        result.append((api, contract))
    return result


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/tables-profile.json')
    expected = dict(apis=apis(version), positions=constants(version),
                    reference_sha256=digest(snapshot.references[version]))
    if (profile['schema_version'] != 'openpine.tables-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or digest(profile['versions'][str(version)]) != digest(expected)):
        raise ValueError('Table ABI differs from pinned evidence.')


def uses_tables(value, *, calls_only=False):
    return drawings.uses_drawing(value, 'table', OPERATIONS, calls_only)


def validate_call(node, version):
    api = next(api for api in (*apis(version), *style_apis(version)) if api['operation'] == node.operation)
    if node.operation in STYLED_CONSTRUCTORS and not any(
            arg.origin == 'source' for arg in node.arguments[4:4 + len(STYLE_DEFAULTS)]):
        raise ValueError('A styled table requires an explicit frame or border option.')
    defaults = api['defaults']
    first_optional = len(api['parameters']) - len(defaults)
    for index, arg in enumerate(node.arguments):
        if arg.origin != 'default':
            continue
        if not first_optional <= index < len(api['parameters']):
            raise ValueError('A required table argument has a default.')
        expected = defaults[index - first_optional]
        expression = arg.expression
        kind, = api['parameters'][index]['types']
        if (expression.value_type != ValueType(kind)
                or (type(expression) is not NAIR if expected is None else
                    type(expression) is not LiteralIR or type(expression.value) is not type(expected)
                    or expression.value != expected)):
            raise ValueError('Table default differs from its closed signature.')


def valid_size(columns, rows):
    return type(columns) is int and type(rows) is int and columns > 0 and rows > 0 and columns * rows <= CELL_LIMIT


def valid_cell_index(column, row, columns, rows):
    return type(column) is int and type(row) is int and 0 <= column < columns and 0 <= row < rows


def valid_text(text):
    return type(text) is str and len(text) <= TEXT_LIMIT and not any('\ud800' <= ch <= '\udfff' for ch in text)


def valid_style(field, value):
    if field.endswith('_width'):
        return type(value) is int and 0 <= value <= MAX_BORDER_WIDTH
    return valid_color(value)


def validate_snapshot(values, next_id):
    if type(values) is not list or len(values) > len(POSITIONS):
        raise ValueError('Invalid live table snapshot.')
    previous, slots, positions = 0, 0, set()
    base_keys = {'id', 'position', 'columns', 'rows', 'bgcolor', 'cells'}
    for table in values:
        if (type(table) is not dict or table.keys() - {'force_overlay'} not in (base_keys, base_keys | STYLE_DEFAULTS.keys())
                or 'force_overlay' in table and table['force_overlay'] is not True
                or 'frame_color' in table and not all(valid_style(field, table[field]) for field in STYLE_DEFAULTS)
                or not drawings.valid_id(table['id']) or not previous < table['id'] < next_id
                or table['position'] not in POSITIONS or table['position'] in positions
                or not valid_size(table['columns'], table['rows']) or not valid_color(table['bgcolor'])
                or type(table['cells']) is not list):
            raise ValueError('Invalid table record.')
        previous = table['id']
        positions.add(table['position'])
        columns, rows = table['columns'], table['rows']
        slots += columns * rows
        if slots > CELL_LIMIT or len(table['cells']) > columns * rows:
            raise ValueError('Table snapshot exceeds the cell limit.')
        previous_cell = (-1, -1)
        for cell in table['cells']:
            if (type(cell) is not dict or cell.keys() != {'column', 'row', 'text', 'text_color', 'bgcolor'}
                    or not valid_cell_index(cell['column'], cell['row'], columns, rows)
                    or (cell['row'], cell['column']) <= previous_cell or not valid_text(cell['text'])
                    or not valid_color(cell['text_color']) or not valid_color(cell['bgcolor'])):
                raise ValueError('Invalid table cell.')
            previous_cell = cell['row'], cell['column']
