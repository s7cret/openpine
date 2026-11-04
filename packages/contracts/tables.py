"""Closed Pine table ABI and bounded pane snapshots."""
from . import drawings
from .colors import constants as color_constants, valid as valid_color
from .model import LiteralIR, NAIR, ValueType
from .wire import digest, read_json

IR_SCHEMA = 'openpine.typed-ir.27'
STATE_SCHEMA = 'openpine.runtime-state.19'
CELL_LIMIT = 256
TEXT_LIMIT = 512
POSITIONS = tuple(f'{y}_{x}' for y in ('top', 'middle', 'bottom') for x in ('left', 'center', 'right'))
OPERATIONS = {
    'table.new': (('position', 'string'), ('columns', 'int'), ('rows', 'int'), ('bgcolor', 'color')),
    'table.cell': (('table_id', 'table'), ('column', 'int'), ('row', 'int'), ('text', 'string'),
                   ('text_color', 'color'), ('bgcolor', 'color')),
    'table.clear': (('table_id', 'table'), ('start_column', 'int'), ('start_row', 'int'),
                    ('end_column', 'int'), ('end_row', 'int')),
    'table.delete': (('table_id', 'table'),),
}


def constants(version):
    return {f'position.{value}': (value, 'string') for value in POSITIONS} if version >= 4 else {}


def apis(version):
    result = drawings.apis(version, 'table', OPERATIONS, [None])
    if result:
        result[1]['defaults'] = ['', color_constants(version)['color.black'][0], None]
        result[2]['defaults'] = [None, None]
    return result


def contracts(version):
    return drawings.contracts(apis(version))


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
    api = next(api for api in apis(version) if api['operation'] == node.operation)
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
                    type(expression) is not LiteralIR or expression.value != expected)):
            raise ValueError('Table default differs from its closed signature.')


def valid_size(columns, rows):
    return type(columns) is int and type(rows) is int and columns > 0 and rows > 0 and columns * rows <= CELL_LIMIT


def valid_cell_index(column, row, columns, rows):
    return type(column) is int and type(row) is int and 0 <= column < columns and 0 <= row < rows


def valid_text(text):
    return type(text) is str and len(text) <= TEXT_LIMIT and not any('\ud800' <= ch <= '\udfff' for ch in text)


def validate_snapshot(values, next_id):
    if type(values) is not list or len(values) > len(POSITIONS):
        raise ValueError('Invalid live table snapshot.')
    previous, slots, positions = 0, 0, set()
    for table in values:
        if (type(table) is not dict or table.keys() != {'id', 'position', 'columns', 'rows', 'bgcolor', 'cells'}
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
