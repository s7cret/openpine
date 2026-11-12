"""Stable call ABI for three window indicators backed by pinned references."""

NAMES = ('swma', 'stoch', 'linreg')
OPERATIONS = frozenset('indicator_window.' + name for name in NAMES)
EFFECTS = ('reads_history', 'updates_state')
LENGTH_INDEX = {'indicator_window.stoch': 3, 'indicator_window.linreg': 1}
SWMA = 'indicator_window.swma'


def parameters(name, version):
    if version < 3:
        return {'swma': ('x',), 'stoch': ('x', 'y', 'z', 'a'), 'linreg': ('x', 'y', 'z')}[name]
    return {
        'swma': ('x' if version < 5 else 'source',),
        'stoch': ('source', 'high', 'low', 'length'),
        'linreg': ('source', 'length', 'offset'),
    }[name]
