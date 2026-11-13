"""Bounded, call-aligned state for SWMA, stochastic and linear regression."""

import copy
import math

from packages.contracts.window_indicators import SWMA
from packages.runtime.history import Ring
from . import KernelError
from .dynamic_windows import (COUNT_LIMIT, PrefixWindow, RangeWindow, bound, capacity, export_window,
                              finite, fork_window, ratio, restore_window)
from .technical import units

STOCH = 'indicator_window.stoch'
LINREG = 'indicator_window.linreg'


def numeric(value):
    return None if value is None else finite(float(value))


class Swma:
    def __init__(self, budget):
        self.values = Ring(budget, 4)

    def update(self, source):
        self.values.append(numeric(source))
        values = [self.values.ago(i) for i in range(4)]
        if None in values:
            return None
        return ratio(sum(w * units(x) for w, x in zip((1, 2, 2, 1), values)), 6 << 1074)


class Stoch:
    def __init__(self, budget, horizon):
        self.high = RangeWindow(budget, 'window.highest', horizon)
        self.low = RangeWindow(budget, 'window.lowest', horizon)

    def update(self, source, high, low, length, *, williams=False):
        source, high, low = map(numeric, (source, high, low))
        bound(length, self.high.horizon)
        if self.high.calls >= COUNT_LIMIT:
            raise KernelError('WINDOW_COUNT', 'Changing window call count exceeds int64.', 'rule.dynamic-window.count')
        size = capacity(self.high.calls + 1, self.high.horizon)
        self.high.budget.preflight(4 * (size - self.high.capacity), 'value')
        ceiling = self.high.update(high, length)
        floor = self.low.update(low, length)
        if source is None or ceiling is None or floor is None or ceiling == floor:
            return None
        bottom = units(floor)
        top = units(ceiling)
        return ratio(100 * (units(source) - (top if williams else bottom)), top - bottom)


class Linreg:
    def __init__(self, budget, horizon):
        # Use the same unweighted and weighted exact prefixes as WMA.
        self.prefix = PrefixWindow(budget, 'window.wma', horizon)
        self.last_missing = 0

    def update(self, source, length, offset):
        if type(offset) is not int:
            raise KernelError('WINDOW_OFFSET', 'Linear regression offset must be a defined int.', 'rule.history')
        source = numeric(source)
        p = self.prefix
        p.push(0.0 if source is None else source, length)
        if source is None:
            self.last_missing = p.calls
        if p.count < length or self.last_missing > p.count - length:
            return None
        sums, weights = p.rings
        total = sums.ago(0) - sums.ago(length)
        if length == 1:
            return ratio(total, 1 << 1074)
        # Translate global weights to 1..length; evaluate the least squares
        # line at x=length-offset in a single final division.
        weighted = weights.ago(0) - weights.ago(length) - (p.count - length) * total
        slope_term = 2 * weighted - (length + 1) * total
        scale = length * length - 1
        return ratio(total * scale + 3 * slope_term * (length - 1 - 2 * offset),
                     (length * scale) << 1074)


TYPES = (Swma, Stoch, Linreg)
BY_KIND = dict(zip((SWMA, STOCH, LINREG), TYPES, strict=True))


def kernel(kind):
    cls = BY_KIND[kind]

    def run(arguments, context):
        if kind == SWMA:
            return context.state(kind, lambda: cls(context.budget)).update(*arguments)
        horizon = context.history_capacity
        bound(arguments[-1 if kind == STOCH else 1], horizon)
        return context.state(kind, lambda: cls(context.budget, horizon)).update(*arguments)

    return run


def fork_indicator(state, budget):
    from packages.runtime.rollback import RingOverlay

    clone = copy.copy(state)
    if type(state) is Swma:
        clone.values = RingOverlay(state.values, budget)
    elif type(state) is Stoch:
        clone.high = fork_window(state.high, budget)
        clone.low = fork_window(state.low, budget)
    else:
        clone.prefix = fork_window(state.prefix, budget)
    return clone


def export_indicator(state):
    if type(state) is Swma:
        ring = state.values
        return SWMA, dict(calls=ring.count, samples=[ring.ago(i) for i in range(min(4, ring.count) - 1, -1, -1)])
    if type(state) is Stoch:
        return STOCH, dict(high=export_window(state.high)[1], low=export_window(state.low)[1])
    return LINREG, dict(prefix=export_window(state.prefix)[1], last_missing=state.last_missing)


def restore_indicator(kind, value, budget, updates, horizon):
    def require(condition):
        if not condition:
            raise ValueError('Invalid window-indicator checkpoint.')

    require(type(value) is dict)
    if kind == SWMA:
        require(horizon == 4 and value.keys() == {'calls', 'samples'})
        calls, samples = value['calls'], value['samples']
        require(type(calls) is int and 1 <= calls <= updates)
        require(type(samples) is list and len(samples) == min(calls, 4))
        require(all(x is None or type(x) is float and math.isfinite(x) for x in samples))
        state = Swma(budget)
        for sample in samples:
            state.values.append(sample)
        if calls > 4:
            shift = calls % 4
            state.values.items[:] = samples[-shift:] + samples[:-shift] if shift else samples
            state.values.count = calls
        return state
    if kind == STOCH:
        require(value.keys() == {'high', 'low'})
        state = Stoch(budget, horizon)
        state.high = restore_window('window.highest', value['high'], budget, updates, horizon)
        state.low = restore_window('window.lowest', value['low'], budget, updates, horizon)
        require((state.high.calls, state.high.length) == (state.low.calls, state.low.length))
        return state
    require(kind == LINREG and value.keys() == {'prefix', 'last_missing'})
    state = object.__new__(Linreg)
    state.prefix = p = restore_window('window.wma', value['prefix'], budget, updates, horizon)
    missing = value['last_missing']
    require(p.count == p.calls <= COUNT_LIMIT and type(missing) is int and 0 <= missing <= p.calls)
    if missing and (age := p.calls - missing) < horizon:
        require(p.rings[0].ago(age) == p.rings[0].ago(age + 1))
    state.last_missing = missing
    return state
