"""Period changes and bounded OHLC accumulators for Pivot Points."""

from fractions import Fraction

from packages.contracts.calendar import floor_open, timeframe
from packages.contracts.pivot_levels import LEVELS, LEVEL_COUNT, METHODS
from . import KernelError
from .technical import number, require, shape


EMPTY = (None,) * LEVEL_COUNT
ALIASES = {'1D': 'D', '1W': 'W', '1M': 'M'}
CALENDAR_MINUTES = {'D': 1440, 'W': 10080, 'M': 40320}


def timeframe_change(arguments, context):
    current, previous, chart_period, requested = arguments
    try:
        period = timeframe(ALIASES.get(requested, requested) if requested else chart_period)
        target = int(period) if period.isdigit() else CALENDAR_MINUTES[period]
        chart = int(chart_period) if chart_period.isdigit() else CALENDAR_MINUTES[chart_period]
        if target < chart:
            raise ValueError('Target precedes the chart resolution.')
        return previous is None or floor_open(current, period) != floor_open(previous, period)
    except ValueError as error:
        raise KernelError('TIMEFRAME_CHANGE_PERIOD', 'Use an equal or higher UTC timeframe in minutes or D/W/M.',
                          'rule.environment') from error


def calculate(method, period, current_open):
    if period is None:
        return EMPTY
    opened, high, low, close = period
    if high is None or low is None or high < low:
        return EMPTY
    if method == 'Woodie' and current_open is None or method != 'Woodie' and close is None:
        return EMPTY
    if method == 'DM' and opened is None:
        return EMPTY
    high, low = map(Fraction, (high, low))
    spread = high - low
    if method == 'Woodie':
        middle = (high + low + 2 * Fraction(current_open)) / 4
    else:
        close = Fraction(close)
        middle = (high + low + close) / 3
    if method == 'DM':
        bias = close if close == opened else high if close > opened else low
        middle = (high + low + close + bias) / 4
        pairs = [(2 * middle - low, 2 * middle - high)]
    elif method == 'Fibonacci':
        pairs = [(middle + spread * scale, middle - spread * scale)
                 for scale in (Fraction(382, 1000), Fraction(618, 1000), 1)]
    elif method == 'Camarilla':
        pairs = [(close + spread * Fraction(11, divisor), close - spread * Fraction(11, divisor))
                 for divisor in (120, 60, 40, 20)]
        resistance = high / low * close if low else None
        pairs.append((resistance, 2 * close - resistance if resistance is not None else None))
    else:
        r1, s1 = 2 * middle - low, 2 * middle - high
        pairs = [(r1, s1), (middle + spread, middle - spread)]
        if method == 'Classic':
            pairs.extend((middle + spread * scale, middle - spread * scale) for scale in (2, 3))
        elif method == 'Woodie':
            pairs.extend((r1 + spread * scale, s1 - spread * scale) for scale in (1, 2))
        else:
            pairs.append((r1 + spread, s1 - spread))
            pairs.extend((middle * scale + high - scale * low,
                          middle * scale - scale * high + low) for scale in (3, 4))
    values = (middle, *(value for pair in pairs for value in pair))
    try:
        result = tuple(float(value) if value is not None else None for value in values)
    except OverflowError as error:
        raise KernelError('PIVOT_LEVEL_RANGE', 'Pivot level exceeds the finite price range.', 'rule.technical.numeric') from error
    return result + EMPTY[len(result):]


def accumulate(period, bar):
    if period is None:
        return bar
    opened, high, low, close = period
    return (opened if opened is not None else bar[0],
            bar[1] if high is None else high if bar[1] is None else max(high, bar[1]),
            bar[2] if low is None else low if bar[2] is None else min(low, bar[2]),
            bar[3] if bar[3] is not None else close)


class PivotLevels:
    def __init__(self):
        self.period = None
        self.levels = EMPTY

    def update(self, arguments, publish):
        opened, high, low, close, method, anchor, developing = arguments
        if method not in METHODS:
            raise KernelError('PIVOT_LEVEL_TYPE', 'Unknown Pivot Points calculation type.', 'rule.technical')
        if method == 'Woodie' and developing is True:
            raise KernelError('PIVOT_LEVEL_WOODIE', 'Woodie requires developing=false.', 'rule.technical')
        bar = (opened, high, low, close)
        period = accumulate(None if anchor is True else self.period, bar)
        levels = calculate(method, self.period, opened) if anchor is True else self.levels
        result = publish(calculate(method, period, None) if developing is True else levels)
        self.period, self.levels = period, levels
        return result


def pivot_levels(arguments, context):
    return context.state(LEVELS, PivotLevels).update(arguments, context.publish_float_array)


def export(state):
    return dict(period=list(state.period), levels=list(state.levels))


def restore(value, updates):
    shape(value, ('period', 'levels'))
    period, levels = value['period'], value['levels']
    require(updates > 0 and type(period) is list and len(period) == 4 and type(levels) is list and len(levels) == LEVEL_COUNT)
    state = PivotLevels()
    state.period = tuple(map(number, period))
    state.levels = tuple(map(number, levels))
    require(state.levels[0] is not None or state.levels == EMPTY)
    return state
