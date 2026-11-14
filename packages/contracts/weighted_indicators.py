"""Versioned contracts for ALMA, HMA and Center of Gravity."""

from math import isqrt


ALMA = 'weighted.alma'
HMA = 'weighted.hma'
COG = 'weighted.cog'
FIRST_VERSION = {ALMA: 1, HMA: 4, COG: 1}
OPERATIONS = frozenset(FIRST_VERSION)
EFFECTS = ('reads_history', 'updates_state')


def length_qualifier(operation, version):
    return 'series' if operation != HMA and version >= 4 else 'simple'


def parameters(operation, version):
    names = ('series', 'windowsize' if version < 3 else 'length') if operation == ALMA else (
        ('x', 'y') if operation == COG and version < 3 else ('source', 'length'))
    fields = ((names[0], ('int', 'float'), 'series'), (names[1], ('int',), length_qualifier(operation, version)))
    if operation != ALMA:
        return fields
    fields += (('offset', ('int', 'float'), 'simple'), ('sigma', ('int', 'float'), 'simple'))
    return (*fields, ('floor', ('bool',), 'simple')) if version >= 4 else fields


def storage(operation, horizon):
    if operation == ALMA:
        return horizon, 'value', 'reached_calls'
    cells = 2 * (horizon + 1)
    if operation == HMA:
        cells += 2 * (isqrt(horizon) + 1)
    return cells, 'exact', 'defined_samples'
