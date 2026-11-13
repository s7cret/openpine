"""Versioned swing-pivot signatures and physical source binding."""

from .technical import validate_market_arguments


HIGH = 'pivot.pivothigh'
LOW = 'pivot.pivotlow'
MARKET = {HIGH: 'high', LOW: 'low'}
OPERATIONS = frozenset(MARKET)
EFFECTS = ('reads_history', 'updates_state')
LEGS = ('leftbars', 'rightbars')
SOURCE = ('source', ('int', 'float'), 'series')


def parameters(version):
    types = ('int', 'float') if version >= 5 else ('int',)
    qualifier = 'series' if version >= 3 else 'simple'
    return (SOURCE, *((name, types, qualifier) for name in LEGS))


def validate_call(node):
    slots = (MARKET[node.operation],) if node.overload_id.endswith('.implicit') else ()
    validate_market_arguments(node.arguments, slots)
    if any(arg.origin != 'source' for arg in node.arguments[len(slots):]):
        raise ValueError('Pivot parameters must come from the source.')
