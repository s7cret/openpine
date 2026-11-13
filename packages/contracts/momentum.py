"""Versioned oscillator parameters and implicit Williams %R market inputs."""

DEV = 'momentum.dev'
CCI = 'momentum.cci'
CMO = 'momentum.cmo'
TSI = 'momentum.tsi'
WPR = 'momentum.wpr'
FIRST_VERSION = {DEV: 1, CCI: 1, CMO: 4, TSI: 1, WPR: 4}
OPERATIONS = frozenset(FIRST_VERSION)
DEVIATIONS = frozenset((DEV, CCI))
EFFECTS = ('reads_history', 'updates_state')
WPR_MARKET = ('high', 'low', 'close')
LENGTH_INDEX = {DEV: 1, CCI: 1, CMO: 1, WPR: len(WPR_MARKET)}


def parameters(operation, version):
    length_qualifier = 'series' if version >= 4 and operation != TSI else 'simple'
    if operation == WPR:
        return (('length', ('int',), length_qualifier),)
    source = 'x' if version < 3 else 'series' if operation == CMO else 'source'
    lengths = (('y', 'z') if version < 3 else ('short_length', 'long_length')) if operation == TSI else (
        'y' if version < 3 else 'length',)
    return ((source, ('float', 'int'), 'series'),
            *((name, ('int',), length_qualifier) for name in lengths))
