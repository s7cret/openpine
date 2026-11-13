"""Versioned signatures and retention for numeric order statistics."""

MEDIAN = 'statistics.median'
MODE = 'statistics.mode'
NEAREST = 'statistics.percentile_nearest_rank'
LINEAR = 'statistics.percentile_linear_interpolation'
RANK = 'statistics.percentrank'
TYPED = frozenset((MEDIAN, MODE))
PERCENTILES = frozenset((NEAREST, LINEAR))
SKIP_MISSING = TYPED | {NEAREST}
FIRST_VERSION = {MEDIAN: 4, MODE: 4, NEAREST: 1, LINEAR: 1, RANK: 1}
OPERATIONS = frozenset(FIRST_VERSION)
EFFECTS = ('reads_history', 'updates_state')


def parameters(operation, version):
    source, length, percentage = ('x', 'y', 'z') if version < 3 else ('source', 'length', 'percentage')
    fields = ((source, ('int', 'float'), 'series'),
              (length, ('int',), 'series' if version >= 4 else 'simple'))
    if operation in PERCENTILES:
        return (*fields, (percentage, ('int', 'float'), 'simple'))
    return fields
