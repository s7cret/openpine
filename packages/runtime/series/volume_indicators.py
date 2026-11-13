"""Volume accumulators, paired money-flow windows and anchored VWAP."""

import copy
import math

from packages.contracts.calendar import MAX_TIME, floor_open
from packages.contracts.volume_indicators import AD, BANDS, CUMULATIVE, DAILY, MFI, OBV, TYPICAL
from . import KernelError
from .dynamic_windows import COUNT_LIMIT, PrefixWindow, bound, export_window, finite, fork_window, ratio, restore_window
from .signals import EventState, export_signal, restore_signal
from .technical import number, require, shape, units
from .window_indicators import numeric

SUM = 'window.sum'
SCALE = 1 << 1074
MAX_UNIT = units(float.fromhex('0x1.fffffffffffffp+1023'))


def contribution(operation, arguments):
    if operation == AD:
        high, low, close, volume = arguments
        if None in arguments:
            return None
        if high == low:
            return 0.
        high, low, close = map(units, (high, low, close))
        return ratio((2 * close - high - low) * units(volume), (high - low) * SCALE)
    close, previous, volume = arguments
    if None in arguments:
        return None
    if operation == OBV:
        return volume if close > previous else -volume if close < previous else 0.
    if previous != 0:
        return ratio((units(close) - units(previous)) * units(volume), units(previous) * SCALE)
    return None


class MoneyFlow:
    def __init__(self, budget, horizon):
        self.previous = None
        budget.preflight(2, 'exact')
        self.parts = tuple(PrefixWindow(budget, SUM, horizon) for _ in range(2))

    def update(self, volume, source, length):
        source = numeric(source)
        up, down = self.parts
        positive = negative = None
        if None not in (source, self.previous, volume):
            movement = finite(source * volume) if source != self.previous else 0.
            positive = movement if source > self.previous else 0.
            negative = movement if source < self.previous else 0.
            up.budget.preflight(sum(part.count < part.horizon for part in self.parts), 'exact')
        up.push(positive, length)
        down.push(negative, length)
        if source is not None:
            self.previous = source
        if up.count < length:
            return None
        positive, negative = (part.rings[0].ago(0) - part.rings[0].ago(length) for part in self.parts)
        return ratio(100 * positive, positive + negative) if positive + negative else None


def deviation(numerator, denominator):
    if not numerator:
        return 0.
    shift = 2 * ((numerator.bit_length() - denominator.bit_length()) // 2)
    scaled = numerator / (denominator << shift) if shift >= 0 else (numerator << -shift) / denominator
    try:
        return finite(math.ldexp(math.sqrt(scaled), shift // 2))
    except OverflowError as error:
        raise KernelError('VWAP_VALUE_RANGE', 'VWAP deviation exceeds the finite range.', 'rule.technical.numeric') from error


class WeightedPrice:
    def __init__(self):
        self.active = False
        self.day = None
        self.samples = self.weight = self.total = self.squares = 0

    def update(self, volume, source, anchor, *, day=None, bands=False, multiplier=None):
        if anchor is True:
            self.active = True
            self.samples = self.weight = self.total = self.squares = 0
        self.day = day
        if self.active and source is not None and volume:
            if self.samples == COUNT_LIMIT:
                raise KernelError('VWAP_COUNT', 'VWAP sample count exceeds int64.', 'rule.technical.numeric')
            value, weight = units(source), units(volume)
            self.samples += 1
            self.weight += weight
            self.total += weight * value
            if bands:
                self.squares += weight * value * value
        middle = ratio(self.total, self.weight * SCALE) if self.weight else None
        if not bands:
            return middle
        if middle is None or multiplier is None:
            return middle, None, None
        if not multiplier:
            return middle, middle, middle
        # Apply the multiplier before rounding the deviation to a float.
        width = deviation((self.weight * self.squares - self.total ** 2) * units(multiplier) ** 2,
                          (self.weight * SCALE ** 2) ** 2)
        width = math.copysign(width, multiplier)
        return middle, finite(middle + width), finite(middle - width)


def kernel(operation):
    def run(arguments, context):
        if operation in CUMULATIVE:
            state = context.state(operation, lambda: EventState(context.budget, 'cum'))
            return state.update(contribution(operation, arguments))
        if operation == MFI:
            horizon = context.history_capacity
            bound(arguments[-1], horizon)
            state = context.state(operation, lambda: MoneyFlow(context.budget, horizon))
            return state.update(*arguments)
        state = context.state(operation, WeightedPrice)
        if operation in (DAILY, TYPICAL):
            time, volume, *prices = arguments
            try:
                day = floor_open(time, 'D')
            except ValueError as error:
                raise KernelError('VWAP_TIME', 'VWAP bar time is outside the UTC calendar.', 'rule.environment') from error
            source = prices[0] if operation == DAILY else (
                ratio(sum(map(units, prices)), 3 * SCALE) if None not in prices else None)
            return state.update(volume, source, state.day != day, day=day)
        volume, source, anchor, *multipliers = arguments
        return state.update(volume, source, anchor, bands=operation == BANDS,
                            multiplier=multipliers[0] if multipliers else None)

    return run


def fork_volume(operation, state, budget):
    clone = copy.copy(state)
    if operation == MFI:
        clone.parts = tuple(fork_window(part, budget) for part in state.parts)
    return clone


def export_volume(operation, state):
    if operation in CUMULATIVE:
        return export_signal(state)[1]
    if operation == MFI:
        return dict(previous=state.previous, parts=[export_window(part)[1] for part in state.parts])
    return dict(vars(state))


def restore_volume(operation, value, budget, updates, horizon, version):
    if operation in CUMULATIVE:
        return restore_signal('cum', value, budget, updates, 'float', version)
    if operation == MFI:
        shape(value, ('previous', 'parts'))
        rows = value['parts']
        require(type(rows) is list and len(rows) == 2)
        state = object.__new__(MoneyFlow)
        state.previous = number(value['previous'])
        up, down = (restore_window(SUM, row, budget, updates, horizon) for row in rows)
        require((up.calls, up.count, up.length) == (down.calls, down.count, down.length))
        require(up.count < up.calls and (up.count == 0 or state.previous is not None))
        require(all(not a or not b for a, b in zip(rows[0]['samples'], rows[1]['samples'], strict=True)))
        state.parts = up, down
        return state

    shape(value, ('active', 'day', 'samples', 'weight', 'total', 'squares'))
    active, day = value['active'], value['day']
    require(updates >= 1 and type(active) is bool)
    if operation in (DAILY, TYPICAL):
        require(active and type(day) is int and 0 <= day < MAX_TIME and floor_open(day, 'D') == day)
    else:
        require(day is None)
    count, weight, total, squares = (value[key] for key in ('samples', 'weight', 'total', 'squares'))
    require(all(type(n) is int for n in (count, weight, total, squares)))
    require(0 <= count <= min(updates, COUNT_LIMIT) and (active or not count))
    require(count <= weight <= count * MAX_UNIT and (bool(count) == bool(weight)))
    require(abs(total) <= weight * MAX_UNIT)
    if operation == BANDS:
        require(0 <= squares <= weight * MAX_UNIT ** 2 and total ** 2 <= weight * squares)
    else:
        require(squares == 0)
    state = WeightedPrice()
    vars(state).update(value)
    return state
