"""Directional movement, Supertrend and Parabolic SAR with bounded state."""

import copy
import math

from packages.contracts.trend_indicators import DMI, SAR, SUPERTREND
from . import KernelError
from .basic import length_value
from .technical import ATRState, RMAState, export_technical, number, require, restore_rma, shape, true_range


def finite(value):
    if not math.isfinite(value):
        raise KernelError('TREND_VALUE_RANGE', 'Trend calculation exceeds the finite numeric range.',
                          'rule.technical.numeric')
    return value


class DmiState:
    def __init__(self):
        self.movement = (RMAState(), RMAState())
        self.range = RMAState()
        self.adx = RMAState()
        self.di = (None, None)

    def update(self, high, low, previous_close, previous_high, previous_low, di_length, adx_length):
        length_value(di_length)
        length_value(adx_length)
        span = true_range(high, low, previous_close, False)
        plus_dm = minus_dm = None
        if None not in (span, previous_high, previous_low):
            span = finite(span)
            up = finite(high - previous_high)
            down = finite(previous_low - low)
            plus_dm = up if up > down and up > 0 else 0.0
            minus_dm = down if down > up and down > 0 else 0.0
        else:
            span = None
        plus = self.movement[0].update(plus_dm, di_length)
        minus = self.movement[1].update(minus_dm, di_length)
        tr = self.range.update(span, di_length)
        if tr and plus is not None:
            self.di = finite(100.0 * (plus / tr)), finite(100.0 * (minus / tr))
        p, m = self.di
        dx = None
        if p is not None:
            total = p + m
            dx = abs(p - m) / total if total else 0.0
        average = self.adx.update(dx, adx_length)
        return p, m, None if average is None else 100.0 * average


class SupertrendState:
    def __init__(self):
        self.atr = ATRState()
        self.upper = self.lower = None
        self.direction = 1.0

    def update(self, high, low, close, previous_close, factor, length):
        atr = self.atr.update(high, low, previous_close, length)
        if None in (atr, high, low, close, factor):
            self.upper = self.lower = None
            self.direction = 1.0
            return None, self.direction
        middle = high * 0.5 + low * 0.5
        distance = finite(factor * atr)
        upper, lower = finite(middle + distance), finite(middle - distance)
        if self.upper is not None:
            previous_line = self.upper if self.direction > 0 else self.lower
            if upper >= self.upper and (previous_close is None or previous_close <= self.upper):
                upper = self.upper
            if lower <= self.lower and (previous_close is None or previous_close >= self.lower):
                lower = self.lower
            if previous_line == self.upper:
                self.direction = -1.0 if close > upper else 1.0
            else:
                self.direction = 1.0 if close < lower else -1.0
        self.upper, self.lower = upper, lower
        return upper if self.direction > 0 else lower, self.direction


def sar_settings(start, step, maximum):
    return all(type(x) is float and math.isfinite(x) for x in (start, step, maximum)) and (
        0 <= start <= maximum and step >= 0)


class SarState:
    def __init__(self):
        self.settings = None
        self.line = self.extreme = self.acceleration = self.rising = None

    def update(self, high, low, close, previous_close, previous_high, previous_low,
               second_high, second_low, start, step, maximum):
        settings = (start, step, maximum)
        if not sar_settings(*settings) or self.settings not in (None, settings):
            raise KernelError('SAR_ACCELERATION', 'SAR requires fixed nonnegative acceleration, with start at most max.',
                              'rule.technical.numeric')
        self.settings = settings
        if None in (high, low, close, previous_high, previous_low, previous_close):
            return None

        first = self.line is None
        if first:
            self.rising = close > previous_close
            self.line = previous_low if self.rising else previous_high
            self.extreme = high if self.rising else low
            self.acceleration = start
        a = self.acceleration
        delta = self.extreme - self.line
        if not math.isfinite(delta) and a <= 1:
            # The two prices may span a range wider than a finite float.
            line = finite((1.0 - a) * self.line + a * self.extreme)
        else:
            line = finite(self.line + a * delta)
        if self.rising and line > low:
            line, self.rising, self.extreme, self.acceleration = max(high, self.extreme), False, low, start
        elif not self.rising and line < high:
            line, self.rising, self.extreme, self.acceleration = min(low, self.extreme), True, high, start
        elif not first and (high > self.extreme if self.rising else low < self.extreme):
            self.extreme = high if self.rising else low
            self.acceleration = min(maximum, a + step)

        choose = min if self.rising else max
        previous, second = (previous_low, second_low) if self.rising else (previous_high, second_high)
        self.line = choose(line, previous) if second is None else choose(line, previous, second)
        return self.line


STATES = {DMI: DmiState, SUPERTREND: SupertrendState, SAR: SarState}
TYPES = tuple(STATES.values())


def kernel(operation):
    def run(arguments, context):
        return context.state(operation, STATES[operation]).update(*arguments)

    return run


def fork_trend(state):
    clone = copy.copy(state)
    if type(state) is DmiState:
        clone.movement = tuple(copy.copy(part) for part in state.movement)
        clone.range = copy.copy(state.range)
        clone.adx = copy.copy(state.adx)
    elif type(state) is SupertrendState:
        clone.atr = copy.copy(state.atr)
    return clone


def export_trend(operation, state):
    if operation == DMI:
        return dict(parts=[export_technical(p)[1] for p in (*state.movement, state.range, state.adx)],
                    di=list(state.di))
    if operation == SUPERTREND:
        return dict(atr=export_technical(state.atr)[1], upper=state.upper, lower=state.lower,
                    direction=state.direction)
    return dict(settings=list(state.settings), line=state.line, extreme=state.extreme,
                acceleration=state.acceleration, rising=state.rising)


def nonnegative_rma(state):
    require(state.total >= 0 and (state.previous is None or state.previous >= 0))
    return state


def restore_trend(operation, value, updates):
    require(updates >= 1)
    state = STATES[operation]()
    if operation == DMI:
        shape(value, ('parts', 'di'))
        rows = value['parts']
        require(type(rows) is list and len(rows) == 4)
        plus, minus, span, adx = (nonnegative_rma(restore_rma(row, updates)) for row in rows)
        require(plus.length == minus.length == span.length and plus.samples == minus.samples == span.samples)
        require(adx.previous is None or adx.previous <= 1.0)
        di = value['di']
        require(type(di) is list and len(di) == 2)
        p, m = map(number, di)
        require((p is None) == (m is None))
        require((adx.samples == 0) == (p is None))
        if p is not None:
            require(p >= 0 and m >= 0 and span.previous is not None)
        if span.previous:
            require((p, m) == (100.0 * (plus.previous / span.previous), 100.0 * (minus.previous / span.previous)))
        state.movement, state.range, state.adx, state.di = (plus, minus), span, adx, (p, m)
    elif operation == SUPERTREND:
        shape(value, ('atr', 'upper', 'lower', 'direction'))
        state.atr = nonnegative_rma(restore_rma(value['atr'], updates, ATRState))
        state.upper, state.lower = number(value['upper']), number(value['lower'])
        direction = number(value['direction'])
        require(direction in (-1.0, 1.0) and (state.upper is None) == (state.lower is None))
        require((direction == 1.0) if state.upper is None else state.atr.previous is not None)
        state.direction = direction
    else:
        shape(value, ('settings', 'line', 'extreme', 'acceleration', 'rising'))
        settings = value['settings']
        require(type(settings) is list and len(settings) == 3 and sar_settings(*settings))
        line, extreme, acceleration = (number(value[key]) for key in ('line', 'extreme', 'acceleration'))
        rising = value['rising']
        if line is None:
            require(extreme is None and acceleration is None and rising is None)
        else:
            require(extreme is not None and type(rising) is bool and acceleration is not None)
            require(settings[0] <= acceleration <= settings[2])
        state.settings = tuple(settings)
        state.line, state.extreme, state.acceleration, state.rising = line, extreme, acceleration, rising
    return state
