"""Current table state, shared immutably with intrabar preview branches."""
from dataclasses import replace

from packages.contracts.colors import valid as valid_color
from packages.contracts.drawings import EVENT_LIMIT, valid_id
from packages.contracts.model import (
    LayoutTableCell, MergedTableRecord, OverlayMergedTableRecord, OverlayStyledMergedTableRecord,
    OverlayStyledTableRecord, OverlayTableRecord, StyledMergedTableRecord, StyledTableRecord, TableCell,
    TableMerge, TableRecord,
)
from packages.contracts.tables import (
    CELL_LAYOUT, CELL_LAYOUT_DEFAULTS, CELL_LAYOUT_SETTERS, CELL_LAYOUT_TOKENS, CELL_LIMIT, CELL_SETTERS,
    CONSTRUCTOR, DEFAULT_TEXT_COLOR, MAX_BORDER_WIDTH, MERGE, OPERATIONS, OVERLAY_CONSTRUCTOR, POSITIONS,
    STYLED_CONSTRUCTORS, STYLED_OVERLAY_CONSTRUCTOR, STYLE_DEFAULTS, TABLE_SETTERS, merge_order,
    merges_overlap, table_payload, valid_cell_index, valid_cell_layout, valid_size, valid_style,
    valid_text, validate_snapshot,
)
from .series import KernelError

RECORDS = {
    (False, False, False): TableRecord, (False, True, False): OverlayTableRecord,
    (True, False, False): StyledTableRecord, (True, True, False): OverlayStyledTableRecord,
    (False, False, True): MergedTableRecord, (False, True, True): OverlayMergedTableRecord,
    (True, False, True): StyledMergedTableRecord, (True, True, True): OverlayStyledMergedTableRecord,
}


def fail(code, message):
    raise KernelError('TABLE_' + code, message, 'rule.tables')


class Tables:
    def __init__(self):
        self.next_id = 1
        self.live = {}
        self.calls = 0

    def fork(self):
        branch = Tables()
        branch.next_id = self.next_id
        branch.live = self.live.copy()
        return branch

    def snapshot(self):
        return [table_payload(table) for table in self.live.values()]

    def checkpoint(self):
        return {'next_id': self.next_id, 'live': self.snapshot()}

    @classmethod
    def restore(cls, state, completed):
        if (type(state) is not dict or state.keys() != {'next_id', 'live'}
                or type(state['next_id']) is not int
                or not 1 <= state['next_id'] <= min(2**63 - 1, completed * EVENT_LIMIT + 1)):
            raise ValueError('Invalid table checkpoint state.')
        validate_snapshot(state['live'], state['next_id'])
        restored = cls()
        restored.next_id = state['next_id']
        for table in state['live']:
            record_type = RECORDS['frame_color' in table, 'force_overlay' in table, 'merges' in table]
            fields = {**table, 'cells': tuple((LayoutTableCell if 'width' in cell else TableCell)(**cell)
                                               for cell in table['cells'])}
            if 'merges' in table:
                fields['merges'] = tuple(TableMerge(**merge) for merge in table['merges'])
            record = record_type(**fields)
            restored.live[record.id] = record
        return restored

    def apply(self, operation, arguments):
        if self.calls >= EVENT_LIMIT:
            fail('CALL_LIMIT', f'A bar allows at most {EVENT_LIMIT} table calls.')
        self.calls += 1
        if operation in (CONSTRUCTOR, OVERLAY_CONSTRUCTOR):
            return self.create(*arguments)
        if operation in STYLED_CONSTRUCTORS:
            base, options = arguments[:4], arguments[4:]
            style = dict(zip(STYLE_DEFAULTS, options[:len(STYLE_DEFAULTS)], strict=True))
            overlay = operation == STYLED_OVERLAY_CONSTRUCTOR and options[-1]
            return self.create(*base, overlay, style=style)
        identity, *values = arguments
        table = self.live.get(identity)
        if table is None:
            return None
        if operation == 'table.delete':
            del self.live[identity]
        elif operation == MERGE:
            self.merge(table, *values)
        elif operation == 'table.cell':
            self.write_cell(table, *values)
        elif operation in CELL_LAYOUT.values():
            fields = dict(zip((name for name, _ in OPERATIONS[operation][1:]), values, strict=True))
            self.write_cell(table, **fields)
        elif operation in TABLE_SETTERS:
            field, _ = TABLE_SETTERS[operation]
            self.set_property(table, field, values[0])
        elif operation in CELL_SETTERS:
            field, _ = CELL_SETTERS[operation]
            self.set_cell_property(table, field, *values)
        elif operation in CELL_LAYOUT_SETTERS:
            self.set_cell_property(table, CELL_LAYOUT_SETTERS[operation], *values)
        else:
            self.clear(table, *values)
        return None

    def create(self, position, columns, rows, bgcolor, force_overlay=False, *, style=None):
        if position not in POSITIONS or not valid_size(columns, rows) or not valid_color(bgcolor):
            fail('VALUE', f'A table needs a valid position, RGBA or na, and dimensions within {CELL_LIMIT} cells.')
        if style is not None:
            for field, value in style.items():
                self.check_style(field, value)
        existing = self.at_position(position)
        slots = sum(t.columns * t.rows for t in self.live.values() if t is not existing)
        if slots + columns * rows > CELL_LIMIT:
            fail('CELL_LIMIT', f'Live tables may allocate at most {CELL_LIMIT} cells.')
        identity = self.next_id
        if not valid_id(identity):
            fail('ID_LIMIT', 'Table identities are exhausted.')
        record = self.record(identity, position, columns, rows, bgcolor, (), force_overlay, style)
        if existing is not None:
            del self.live[existing.id]
        self.live[identity] = record
        self.next_id += 1
        return identity

    def at_position(self, position):
        return next((table for table in self.live.values() if table.position == position), None)

    @staticmethod
    def record(identity, position, columns, rows, bgcolor, cells, overlay, style, *, merges=()):
        record_type = RECORDS[style is not None, overlay is True, bool(merges)]
        fields = dict(style or {})
        if overlay is True:
            fields['force_overlay'] = True
        if merges:
            fields['merges'] = merges
        return record_type(identity, position, columns, rows, bgcolor, cells, **fields)

    @staticmethod
    def check_style(field, value):
        if not valid_style(field, value):
            fail('STYLE', f'{field} requires RGBA or na for colors and integers in 0..{MAX_BORDER_WIDTH} for widths.')

    def set_property(self, table, field, value):
        if field == 'position':
            if value not in POSITIONS:
                fail('VALUE', 'Expected a valid table position.')
            if value == table.position:
                return
            resident = self.at_position(value)
            if resident is not None:
                del self.live[min(table.id, resident.id)]
                if resident.id > table.id:
                    return
        else:
            self.check_style(field, value)
            if field in STYLE_DEFAULTS and not isinstance(table, StyledTableRecord):
                self.live[table.id] = self.record(table.id, table.position, table.columns, table.rows,
                    table.bgcolor, table.cells, getattr(table, 'force_overlay', False), {**STYLE_DEFAULTS, field: value},
                    merges=getattr(table, 'merges', ()))
                return
        self.live[table.id] = replace(table, **{field: value})

    def check_index(self, table, column, row):
        if not valid_cell_index(column, row, table.columns, table.rows):
            fail('INDEX', 'Table cell indices are outside the allocated grid.')

    def write_cell(self, table, column, row, text, text_color, bgcolor, **layout):
        self.check_index(table, column, row)
        cell = TableCell(column, row, '' if text is None else text, text_color, bgcolor)
        self.put_cell(table, self.layout_cell(cell, layout) if layout else cell)

    @staticmethod
    def layout_cell(cell, updates):
        layout = {field: getattr(cell, field, default) for field, default in CELL_LAYOUT_DEFAULTS.items()}
        for field, value in updates.items():
            if field in CELL_LAYOUT_TOKENS and type(value) is str:
                value = value.removeprefix(CELL_LAYOUT_TOKENS[field][0])
            if field in ('width', 'height') and type(value) is int and abs(value) < 2**63:
                value = float(value)
            if field == 'tooltip' and value is None:
                value = ''
            if not valid_cell_layout(field, value):
                fail('CELL_LAYOUT', f'Invalid table cell {field}.')
            layout[field] = value
        base = (cell.column, cell.row, cell.text, cell.text_color, cell.bgcolor)
        if layout == CELL_LAYOUT_DEFAULTS:
            return TableCell(*base)
        return LayoutTableCell(*base, **layout)

    def set_cell_property(self, table, field, column, row, value):
        self.check_index(table, column, row)
        cell = next((cell for cell in table.cells if (cell.column, cell.row) == (column, row)), None)
        if cell is None:
            cell = TableCell(column, row, '', DEFAULT_TEXT_COLOR, None)
        if field in CELL_LAYOUT_DEFAULTS:
            self.put_cell(table, self.layout_cell(cell, {field: value}))
        else:
            if field == 'text' and value is None:
                value = ''
            self.put_cell(table, replace(cell, **{field: value}))

    def put_cell(self, table, cell):
        if not valid_text(cell.text) or not valid_color(cell.text_color) or not valid_color(cell.bgcolor):
            fail('VALUE', 'Table cell text or color is outside the profile.')
        cells = [current for current in table.cells if (current.row, current.column) != (cell.row, cell.column)]
        cells.append(cell)
        cells.sort(key=lambda cell: (cell.row, cell.column))
        self.live[table.id] = replace(table, cells=tuple(cells))

    def merge(self, table, start_column, start_row, end_column, end_row):
        self.check_index(table, start_column, start_row)
        self.check_index(table, end_column, end_row)
        if start_column > end_column or start_row > end_row:
            fail('MERGE_RANGE', 'Table merge requires an ordered rectangle.')
        if (start_column, start_row) == (end_column, end_row):
            return
        merged = TableMerge(start_column, start_row, end_column, end_row)
        existing = getattr(table, 'merges', ())
        if any(merges_overlap(merged, previous) for previous in existing):
            fail('MERGE_OVERLAP', 'Table merges cannot share a cell.')
        merges = tuple(sorted((*existing, merged), key=merge_order))
        if existing:
            self.live[table.id] = replace(table, merges=merges)
        else:
            style = {name: getattr(table, name) for name in STYLE_DEFAULTS} if isinstance(table, StyledTableRecord) else None
            self.live[table.id] = self.record(table.id, table.position, table.columns, table.rows,
                table.bgcolor, table.cells, getattr(table, 'force_overlay', False), style, merges=merges)

    def clear(self, table, start_column, start_row, end_column, end_row):
        end_column = start_column if end_column is None else end_column
        end_row = start_row if end_row is None else end_row
        self.check_index(table, start_column, start_row)
        self.check_index(table, end_column, end_row)
        if end_column < start_column or end_row < start_row:
            fail('INDEX', 'Table clear requires an ordered rectangle.')
        cells = tuple(cell for cell in table.cells if not (
            start_column <= cell.column <= end_column and start_row <= cell.row <= end_row))
        self.live[table.id] = replace(table, cells=cells)
