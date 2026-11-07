"""Current table state, shared immutably with intrabar preview branches."""
from dataclasses import asdict, replace

from packages.contracts.colors import valid as valid_color
from packages.contracts.drawings import EVENT_LIMIT, valid_id
from packages.contracts.model import OverlayTableRecord, TableCell, TableRecord
from packages.contracts.tables import (
    CELL_LIMIT, CONSTRUCTOR, OVERLAY_CONSTRUCTOR, POSITIONS, valid_cell_index, valid_size, valid_text, validate_snapshot,
)
from .series import KernelError


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
        records = [asdict(table) for table in self.live.values()]
        for record in records:
            record['cells'] = list(record['cells'])
        return records

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
            record_type = OverlayTableRecord if 'force_overlay' in table else TableRecord
            record = record_type(**{**table, 'cells': tuple(TableCell(**cell) for cell in table['cells'])})
            restored.live[record.id] = record
        return restored

    def apply(self, operation, arguments):
        if self.calls >= EVENT_LIMIT:
            fail('CALL_LIMIT', f'A bar allows at most {EVENT_LIMIT} table calls.')
        self.calls += 1
        if operation in (CONSTRUCTOR, OVERLAY_CONSTRUCTOR):
            return self.create(*arguments)
        identity, *values = arguments
        table = self.live.get(identity)
        if table is None:
            return None
        if operation == 'table.delete':
            del self.live[identity]
        elif operation == 'table.cell':
            self.write_cell(table, *values)
        else:
            self.clear(table, *values)
        return None

    def create(self, position, columns, rows, bgcolor, force_overlay=False):
        if position not in POSITIONS or not valid_size(columns, rows) or not valid_color(bgcolor):
            fail('VALUE', f'A table needs a valid position, RGBA or na, and dimensions within {CELL_LIMIT} cells.')
        existing = next((t for t in self.live.values() if t.position == position), None)
        slots = sum(t.columns * t.rows for t in self.live.values() if t is not existing)
        if slots + columns * rows > CELL_LIMIT:
            fail('CELL_LIMIT', f'Live tables may allocate at most {CELL_LIMIT} cells.')
        identity = self.next_id
        if not valid_id(identity):
            fail('ID_LIMIT', 'Table identities are exhausted.')
        if existing is not None:
            del self.live[existing.id]
        fields = (identity, position, columns, rows, bgcolor, ())
        self.live[identity] = OverlayTableRecord(*fields, force_overlay=True) if force_overlay is True else TableRecord(*fields)
        self.next_id += 1
        return identity

    def check_index(self, table, column, row):
        if not valid_cell_index(column, row, table.columns, table.rows):
            fail('INDEX', 'Table cell indices are outside the allocated grid.')

    def write_cell(self, table, column, row, text, text_color, bgcolor):
        self.check_index(table, column, row)
        if text is None:
            text = ''
        if not valid_text(text) or not valid_color(text_color) or not valid_color(bgcolor):
            fail('VALUE', 'Table cell text or color is outside the profile.')
        cells = [cell for cell in table.cells if (cell.row, cell.column) != (row, column)]
        cells.append(TableCell(column, row, text, text_color, bgcolor))
        cells.sort(key=lambda cell: (cell.row, cell.column))
        self.live[table.id] = replace(table, cells=tuple(cells))

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
