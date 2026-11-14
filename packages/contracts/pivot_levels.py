"""Calendar anchor and OHLC pivot-level call contracts."""

from .model import LiteralIR
from .technical import validate_market_arguments


CHANGE = 'environment.timeframe_change'
LEVELS = 'pivot.point_levels'
OPERATIONS = frozenset((CHANGE, LEVELS))
MARKET = {CHANGE: ('time', 'previous_time', 'timeframe.period'), LEVELS: ('open', 'high', 'low', 'close')}
EFFECTS = {CHANGE: ('reads_history',), LEVELS: ('reads_history', 'updates_state', 'allocates_array')}
NAMES = {CHANGE: 'timeframe.change', LEVELS: 'ta.pivot_point_levels'}
METHODS = frozenset(('Traditional', 'Fibonacci', 'Woodie', 'Classic', 'DM', 'Camarilla'))
LEVEL_COUNT = 11


def parameters(operation):
    if operation == CHANGE:
        return (('timeframe', 'string'),)
    return (('type', 'string'), ('anchor', 'bool'), ('developing', 'bool'))


def validate_call(node):
    slots = MARKET[node.operation]
    validate_market_arguments(node.arguments, slots)
    supplied = node.arguments[len(slots):]
    if any(argument.origin != 'source' for argument in supplied[:-1]):
        raise ValueError('Only the final pivot-level argument has a default.')
    final = supplied[-1]
    default = '' if node.operation == CHANGE else False
    if final.origin != 'source' and (
        not isinstance(final.expression, LiteralIR) or type(final.expression.value) is not type(default)
        or final.expression.value != default
    ):
        raise ValueError('The pivot-level default differs from its contract.')
