"""Content-addressed chunks of confirmed label mutations."""
from packages.contracts.labels import EVENT_LIMIT, valid_id, validate_label
from packages.contracts.wire import canonical

SCHEMA = 'openpine.label-journal.1'
CHUNK_BYTES = 256 * 1024
MAX_PART_BYTES = 512 * 1024


def validate_event(event):
    if type(event) is not dict:
        raise ValueError('Invalid label event.')
    action = event.get('action')
    if action == 'delete':
        if event.keys() != {'action', 'id', 'reason'} or not valid_id(event['id']) or event['reason'] not in ('explicit', 'capacity'):
            raise ValueError('Invalid label deletion.')
    elif action in ('create', 'update') and event.keys() == {'action', 'label'}:
        validate_label(event['label'])
    else:
        raise ValueError('Unknown label mutation.')


class LabelJournal:
    def __init__(self, objects):
        self.objects = objects
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
        return {'schema_version': SCHEMA, 'records': self.records,
                'parts': self.parts + ([tail] if tail is not None else [])}

    def checkpoint(self):
        return {'schema_version': SCHEMA, 'records': self.records,
                'parts': list(self.parts), 'pending': self.tail()}

    def restore(self, state, completed):
        if (type(state) is not dict or state.keys() != {'schema_version', 'records', 'parts', 'pending'}
                or state['schema_version'] != SCHEMA or type(state['parts']) is not list
                or type(state['records']) is not int or not 0 <= state['records'] <= completed * EVENT_LIMIT):
            raise ValueError('Invalid label journal prefix.')
        tail = state['pending']
        total, end = 0, 0
        for part in state['parts'] + ([tail] if tail is not None else []):
            if (type(part) is not dict or part.keys() != {'object_id', 'start', 'end', 'rows'}
                    or any(type(part[k]) is not int for k in ('start', 'end', 'rows'))
                    or not max(0, end - 1) <= part['start'] < part['end'] <= completed
                    or not 1 <= part['rows'] <= CHUNK_BYTES
                    or self.objects.verify(part['object_id']).stat().st_size > MAX_PART_BYTES):
                raise ValueError('Invalid label journal chunk.')
            total += part['rows']
            end = part['end']
        if total != state['records']:
            raise ValueError('Label journal count differs from its chunks.')
        rows = list(part_rows(self.objects, tail)) if tail is not None else []
        self.parts, self.records, self.pending = list(state['parts']), total, tail
        self.rows, self.size = rows, sum(len(canonical(row)) + 1 for row in rows)


def part_rows(objects, part):
    rows = objects.get(part['object_id'], max_bytes=MAX_PART_BYTES)
    if type(rows) is not list or len(rows) != part['rows'] or not rows:
        raise ValueError('Invalid label journal rows.')
    previous = part['start']
    for row in rows:
        if (type(row) is not dict or row.keys() != {'bar_index', 'time', 'event'}
                or type(row['bar_index']) is not int or not previous <= row['bar_index'] < part['end']
                or type(row['time']) is not int or not 0 <= row['time'] < 2**63):
            raise ValueError('Invalid label event boundary.')
        validate_event(row['event'])
        previous = row['bar_index']
    if rows[0]['bar_index'] != part['start'] or previous + 1 != part['end']:
        raise ValueError('Label journal chunk does not match its bounds.')
    yield from rows


def label_events(objects, publication, start, limit):
    offset = 0
    for part in publication['parts']:
        end = offset + part['rows']
        if start < end:
            for row in part_rows(objects, part):
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
