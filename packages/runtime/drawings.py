"""Bounded drawing lifecycle shared by labels and lines."""
from packages.contracts.drawings import EVENT_LIMIT, LIMIT, PLURALS, valid_id, validate_snapshot
from packages.contracts.drawing_xloc import TOKENS
from packages.contracts.drawing_xloc_mutations import GETTERS, SETTERS
from packages.runtime.series import KernelError


class Drawings:
    def __init__(self):
        self.next_id = 1
        self.event_count = 0
        self.live = {}
        self.pending = []

    def fork(self):
        branch = type(self)()
        branch.next_id = self.next_id
        branch.event_count = self.event_count
        branch.live = self.live.copy()
        return branch

    def snapshot(self):
        return [record.copy() for record in self.live.values()]

    def checkpoint(self):
        return {'next_id': self.next_id, 'event_count': self.event_count, 'live': self.snapshot()}

    @classmethod
    def restore(cls, state, completed):
        if (type(state) is not dict or state.keys() != {'next_id', 'event_count', 'live'}
                or type(state['next_id']) is not int
                or not 1 <= state['next_id'] <= min(2**63 - 1, completed * EVENT_LIMIT + 1)
                or type(state['event_count']) is not int
                or not state['next_id'] - 1 <= state['event_count'] <= completed * EVENT_LIMIT):
            raise ValueError(f'Invalid {cls.kind} checkpoint state.')
        validate_snapshot(state['live'], state['next_id'], cls.kind, cls.validate)
        result = cls()
        result.next_id = state['next_id']
        result.event_count = state['event_count']
        result.live = {record['id']: record.copy() for record in state['live']}
        return result

    def reserve(self, count):
        if len(self.pending) + count > EVENT_LIMIT:
            raise KernelError(f'{self.kind.upper()}_EVENT_LIMIT',
                f'A bar allows at most {EVENT_LIMIT} {self.kind} events.', f'rule.{PLURALS[self.kind]}')

    def remove(self, identity, reason):
        del self.live[identity]
        self.pending.append({'action': 'delete', 'id': identity, 'reason': reason})

    def apply(self, operation, arguments):
        action = operation.split('.', 1)[1]
        if action == 'new':
            return self.create(dict(zip(self.fields, arguments, strict=True)))
        if action == 'new_xloc':
            *values, xloc = arguments
            return self.create(dict(zip(self.fields, values, strict=True), xloc=coordinate_mode(xloc)))
        identity, *values = arguments
        if identity not in self.live:
            return None
        if action == 'delete':
            self.reserve(1)
            self.remove(identity, 'explicit')
        elif operation in GETTERS:
            return self.live[identity][GETTERS[operation]]
        elif operation in SETTERS:
            *coordinates, xloc = values
            record = {**self.live[identity], **dict(zip(SETTERS[operation], coordinates, strict=True)),
                      'xloc': coordinate_mode(xloc)}
            if self.kind == 'line':
                record.setdefault('extend', 'none')
            self.write('update', record)
        else:
            record = {**self.live[identity], **dict(zip(self.updates[action], values, strict=True))}
            self.write('update', record)
        return None

    def create(self, record):
        identity = self.next_id
        if not valid_id(identity):
            raise KernelError(f'{self.kind.upper()}_ID_LIMIT',
                f'{self.kind.capitalize()} identities are exhausted.', f'rule.{PLURALS[self.kind]}')
        self.write('create', {**record, 'id': identity})
        self.next_id += 1
        return identity

    def write(self, action, record):
        try:
            self.validate(record)
        except ValueError as error:
            raise KernelError(f'{self.kind.upper()}_VALUE', self.invalid_record, f'rule.{PLURALS[self.kind]}') from error
        evict = action == 'create' and len(self.live) == LIMIT
        self.reserve(1 + evict)
        if evict:
            self.remove(next(iter(self.live)), 'capacity')
        self.live[record['id']] = record
        self.pending.append({'action': action, self.kind: record})


def coordinate_mode(value):
    if value in TOKENS:
        return value.removeprefix('xloc.')
    raise KernelError('DRAWING_XLOC', 'Expected xloc.bar_index or xloc.bar_time.', 'rule.drawing_xloc')
