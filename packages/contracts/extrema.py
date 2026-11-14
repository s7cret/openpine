"""Versioned numeric range and cumulative extrema operations."""

SPREAD = 'extrema.range'
MAXIMUM = 'extrema.max'
MINIMUM = 'extrema.min'
CUMULATIVE = frozenset((MAXIMUM, MINIMUM))
OPERATIONS = CUMULATIVE | {SPREAD}
EFFECTS = ('reads_history', 'updates_state')
SPREAD_CELL = 'pair'
