"""Closed ABI for trend indicators and their physical market inputs."""

DMI = 'trend.dmi'
SUPERTREND = 'trend.supertrend'
SAR = 'trend.sar'
OPERATIONS = (DMI, SUPERTREND, SAR)
EFFECTS = ('reads_history', 'updates_state')

MARKET = {
    DMI: ('high', 'low', 'previous_close', 'previous_high', 'previous_low'),
    SUPERTREND: ('high', 'low', 'close', 'previous_close'),
    SAR: ('high', 'low', 'close', 'previous_close', 'previous_high', 'previous_low', 'second_high', 'second_low'),
}


def parameters(operation, version):
    if operation == DMI:
        return (('diLength', 'int', 'simple'), ('adxSmoothing', 'int', 'simple'))
    if operation == SUPERTREND:
        return (('factor', 'float', 'series'), ('atrPeriod', 'int', 'simple'))
    names = ('x', 'y', 'z') if version < 3 else ('start', 'inc', 'max')
    return tuple((name, 'float', 'simple') for name in names)


def validate_call(node):
    from .technical import validate_market_arguments

    slots = MARKET[node.operation]
    validate_market_arguments(node.arguments, slots)
    if any(arg.origin != 'source' for arg in node.arguments[len(slots):]):
        raise ValueError('Required trend indicator arguments must come from the source.')
