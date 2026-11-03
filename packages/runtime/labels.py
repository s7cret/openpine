"""Bounded label state owned by one confirmed evaluation or preview branch."""
from packages.contracts.labels import EVENT_LIMIT, LIMIT, TEXT_LIMIT, valid_id, validate_label, validate_snapshot
from packages.runtime.series import KernelError


class Labels:
    def __init__(self):
        self.next_id = 1
        self.event_count = 0
        self.live = {}
        self.pending = []

    def fork(self):
        branch = Labels()
        branch.next_id = self.next_id
        branch.event_count = self.event_count
        branch.live = self.live.copy()
        return branch

    def snapshot(self):
        return [label.copy() for label in self.live.values()]

    def checkpoint(self):
        return {'next_id': self.next_id, 'event_count': self.event_count, 'live': self.snapshot()}

    @classmethod
    def restore(cls, state, completed):
        if (type(state) is not dict or state.keys() != {'next_id', 'event_count', 'live'}
                or type(state['next_id']) is not int
                or not 1 <= state['next_id'] <= min(2**63 - 1, completed * EVENT_LIMIT + 1)
                or type(state['event_count']) is not int
                or not state['next_id'] - 1 <= state['event_count'] <= completed * EVENT_LIMIT):
            raise ValueError('Invalid label checkpoint state.')
        result = cls()
        result.next_id = state['next_id']
        result.event_count = state['event_count']
        validate_snapshot(state['live'], result.next_id)
        result.live = {label['id']: label.copy() for label in state['live']}
        return result

    def reserve(self, count):
        if len(self.pending) + count > EVENT_LIMIT:
            raise KernelError('LABEL_EVENT_LIMIT', f'A bar allows at most {EVENT_LIMIT} label events.', 'rule.labels')

    def remove(self, identity, reason):
        del self.live[identity]
        self.pending.append({'action': 'delete', 'id': identity, 'reason': reason})

    def apply(self, operation, arguments):
        if operation == 'label.new':
            x, y, text = arguments
            identity = self.next_id
            if not valid_id(identity):
                raise KernelError('LABEL_ID_LIMIT', 'Label identities are exhausted.', 'rule.labels')
            record = {'id': identity, 'x': x, 'y': y, 'text': text}
            self.write('create', record)
            self.next_id += 1
            return identity
        identity, *values = arguments
        if identity not in self.live:
            return None
        if operation == 'label.delete':
            self.reserve(1)
            self.remove(identity, 'explicit')
        else:
            record = self.live[identity].copy()
            if operation == 'label.set_xy':
                record['x'], record['y'] = values
            else:
                record['text'] = values[0]
            self.write('update', record)
        return None

    def write(self, action, record):
        try:
            validate_label(record)
        except ValueError as error:
            raise KernelError('LABEL_VALUE', f'Label text requires a string up to {TEXT_LIMIT} characters.', 'rule.labels') from error
        evict = action == 'create' and len(self.live) == LIMIT
        self.reserve(1 + evict)
        if evict:
            self.remove(next(iter(self.live)), 'capacity')
        self.live[record['id']] = record
        self.pending.append({'action': action, 'label': record})
