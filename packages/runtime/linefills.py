"""Linefills share the evaluation, rollback and checkpoint of their lines."""
from packages.contracts.colors import valid as valid_color
from packages.contracts.drawings import EVENT_LIMIT, LIMIT, valid_id
from packages.contracts.linefills import OPERATIONS, READS, pair, validate_snapshot
from .lines import Lines
from .series import KernelError


def fail(code, message):
    raise KernelError('LINEFILL_' + code, message, 'rule.linefills')


class FilledLines(Lines):
    def __init__(self):
        super().__init__()
        self.fills = {}
        self.next_fill_id = 1
        self.fill_calls = 0

    def fork(self):
        branch = super().fork()
        branch.fills = self.fills.copy()
        branch.next_fill_id = self.next_fill_id
        return branch

    def fill_snapshot(self):
        return [fill.copy() for fill in self.fills.values()]

    def checkpoint(self):
        return {**super().checkpoint(), 'fills': {'next_id': self.next_fill_id, 'live': self.fill_snapshot()}}

    @classmethod
    def restore(cls, state, completed):
        if type(state) is not dict or 'fills' not in state:
            raise ValueError('Missing linefill checkpoint state.')
        lines = super().restore({k: v for k, v in state.items() if k != 'fills'}, completed)
        fills = state['fills']
        if (type(fills) is not dict or fills.keys() != {'next_id', 'live'}
                or type(fills['next_id']) is not int
                or not 1 <= fills['next_id'] <= min(2**63 - 1, completed * EVENT_LIMIT + 1)):
            raise ValueError('Invalid linefill checkpoint state.')
        validate_snapshot(fills['live'], fills['next_id'], lines.live.values())
        lines.next_fill_id = fills['next_id']
        lines.fills = {fill['id']: fill.copy() for fill in fills['live']}
        return lines

    def remove(self, identity, reason):
        super().remove(identity, reason)
        for fill_id, fill in list(self.fills.items()):
            if identity in (fill['line1'], fill['line2']):
                del self.fills[fill_id]

    def apply(self, operation, arguments):
        if operation not in OPERATIONS:
            return super().apply(operation, arguments)
        if operation in READS:
            fill = self.fills.get(arguments[0])
            return fill[READS[operation]] if fill is not None else None
        if self.fill_calls >= EVENT_LIMIT:
            fail('CALL_LIMIT', f'A bar allows at most {EVENT_LIMIT} linefill mutation calls.')
        self.fill_calls += 1
        if operation == 'linefill.new':
            return self.create_fill(*arguments)
        identity, *values = arguments
        if identity not in self.fills:
            return None
        if operation == 'linefill.delete':
            del self.fills[identity]
        else:
            color, = values
            if not valid_color(color):
                fail('VALUE', 'Linefill color must be RGBA or na.')
            self.fills[identity] = {**self.fills[identity], 'color': color}
        return None

    def create_fill(self, line1, line2, color):
        if line1 == line2 or line1 not in self.live or line2 not in self.live:
            return None
        identity = self.next_fill_id
        if not valid_color(color):
            fail('VALUE', 'Linefill color must be RGBA or na.')
        if not valid_id(identity):
            fail('ID_LIMIT', 'Linefill identities are exhausted.')
        key = pair(line1, line2)
        previous = next((f['id'] for f in self.fills.values() if pair(f['line1'], f['line2']) == key), None)
        if previous is not None:
            del self.fills[previous]
        elif len(self.fills) == LIMIT:
            del self.fills[next(iter(self.fills))]
        self.fills[identity] = dict(id=identity, line1=line1, line2=line2, color=color)
        self.next_fill_id += 1
        return identity
