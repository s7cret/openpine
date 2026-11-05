"""Content-addressed chunks of confirmed drawing mutations."""
from packages.contracts.drawings import EVENT_LIMIT, JOURNALED_BASES, valid_id
from packages.contracts.wire import canonical

CHUNK_BYTES = 256 * 1024
MAX_PART_BYTES = 512 * 1024


def validate_event(event, kind, validate):
    if type(event) is not dict:
        raise ValueError(f'Invalid {kind} event.')
    action = event.get('action')
    if action == 'delete':
        if event.keys() != {'action', 'id', 'reason'} or not valid_id(event['id']) or event['reason'] not in ('explicit', 'capacity'):
            raise ValueError(f'Invalid {kind} deletion.')
    elif (action == 'create' or action == 'update' and kind != 'polyline') and event.keys() == {'action', kind}:
        validate(event[kind])
    else:
        raise ValueError(f'Unknown {kind} mutation.')


class DrawingJournal:
    def __init__(self, objects, kind, validate):
        self.objects = objects
        self.kind, self.validate = kind, validate
        self.schema = f'openpine.{kind}-journal.1'
        self.parts = []
        self.records = 0
        self.rows = []
        self.size = 0
        self.pending = None

    def emit(self, index, time, events):
        for event in events:
            row = {'bar_index': index, 'time': time, 'event': event}
            size = len(canonical(row)) + 1
            if self.rows and self.size + size > CHUNK_BYTES:
                self.parts.append(self.tail())
                self.rows, self.size = [], 0
            self.rows.append(row)
            self.size += size
            self.records += 1
            self.pending = None

    def tail(self):
        if self.rows and self.pending is None:
            self.pending = {'object_id': self.objects.put(self.rows), 'rows': len(self.rows),
                            'start': self.rows[0]['bar_index'], 'end': self.rows[-1]['bar_index'] + 1}
        return self.pending

    def publication(self):
        tail = self.tail()
        return {'schema_version': self.schema, 'records': self.records,
                'parts': self.parts + ([tail] if tail is not None else [])}

    def checkpoint(self):
        return {'schema_version': self.schema, 'records': self.records,
                'parts': list(self.parts), 'pending': self.tail()}

    def restore(self, state, completed):
        if (type(state) is not dict or state.keys() != {'schema_version', 'records', 'parts', 'pending'}
                or state['schema_version'] != self.schema or type(state['parts']) is not list
                or type(state['records']) is not int or not 0 <= state['records'] <= completed * EVENT_LIMIT):
            raise ValueError(f'Invalid {self.kind} journal prefix.')
        tail = state['pending']
        total, end = 0, 0
        for part in state['parts'] + ([tail] if tail is not None else []):
            if (type(part) is not dict or part.keys() != {'object_id', 'start', 'end', 'rows'}
                    or any(type(part[k]) is not int for k in ('start', 'end', 'rows'))
                    or not max(0, end - 1) <= part['start'] < part['end'] <= completed
                    or not 1 <= part['rows'] <= CHUNK_BYTES
                    or self.objects.verify(part['object_id']).stat().st_size > MAX_PART_BYTES):
                raise ValueError(f'Invalid {self.kind} journal chunk.')
            total += part['rows']
            end = part['end']
        if total != state['records']:
            raise ValueError(f'{self.kind.capitalize()} journal count differs from its chunks.')
        rows = list(part_rows(self.objects, tail, self.kind, self.validate)) if tail is not None else []
        self.parts, self.records, self.pending = list(state['parts']), total, tail
        self.rows, self.size = rows, sum(len(canonical(row)) + 1 for row in rows)


def part_rows(objects, part, kind, validate):
    rows = objects.get(part['object_id'], max_bytes=MAX_PART_BYTES)
    if type(rows) is not list or len(rows) != part['rows'] or not rows:
        raise ValueError(f'Invalid {kind} journal rows.')
    previous = part['start']
    for row in rows:
        if (type(row) is not dict or row.keys() != {'bar_index', 'time', 'event'}
                or type(row['bar_index']) is not int or not previous <= row['bar_index'] < part['end']
                or type(row['time']) is not int or not 0 <= row['time'] < 2**63):
            raise ValueError(f'Invalid {kind} event boundary.')
        validate_event(row['event'], kind, validate)
        previous = row['bar_index']
    if rows[0]['bar_index'] != part['start'] or previous + 1 != part['end']:
        raise ValueError(f'{kind.capitalize()} journal chunk does not match its bounds.')
    yield from rows


def drawing_events(objects, publication, start, limit, kind, validate):
    if publication['schema_version'] != f'openpine.{kind}-journal.1':
        raise ValueError('Invalid drawing journal schema.')
    offset = 0
    for part in publication['parts']:
        end = offset + part['rows']
        if start < end:
            for row in part_rows(objects, part, kind, validate):
                if offset >= start:
                    yield row
                    limit -= 1
                    if limit == 0:
                        return
                offset += 1
        else:
            offset = end


def mark_chunks(journal, mark):
    if journal:
        for part in journal['parts']:
            mark(part['object_id'])
        if journal.get('pending') is not None:
            mark(journal['pending']['object_id'])


def mark_drawing_chunks(container, mark):
    for kind in JOURNALED_BASES:
        mark_chunks(container.get(f'{kind}_journal'), mark)
