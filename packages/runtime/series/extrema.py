"""Compact checkpoints for a bounded range and scalar cumulative extrema."""

import math

from packages.contracts.extrema import MAXIMUM, SPREAD, SPREAD_CELL
from . import KernelError
from .dynamic_windows import COUNT_LIMIT, RangeWindow, bound, finite, require
from .technical import combine


class SpreadWindow(RangeWindow):
    cell_kind = SPREAD_CELL

    def __init__(self, budget, horizon):
        super().__init__(budget, SPREAD, horizon)

    def merge(self, left, right):
        if left is None:
            return right
        if right is None:
            return left
        return min(left[0], right[0]), max(left[1], right[1])

    def update(self, source, length):
        bound(length, self.horizon)
        if source is not None:
            self.push((source, source))
        if self.calls < length:
            return None
        low, high = self.latest(length)
        result = high - low
        if type(result) is int and result >= 2**63:
            raise KernelError('WINDOW_VALUE_RANGE', 'The integer range exceeds int64.', 'rule.dynamic-window.numeric')
        return finite(result)


class CumulativeExtremum:
    def __init__(self):
        self.value = None

    def update(self, source, highest):
        self.value = combine(self.value, source, highest)
        return None if self.value is None else float(self.value)


def kernel(operation):
    if operation == SPREAD:
        def spread(arguments, context):
            source, length = arguments
            horizon = context.history_capacity
            bound(length, horizon)
            state = context.state(SPREAD, lambda: SpreadWindow(context.budget, horizon))
            return state.update(source, length)
        return spread

    def cumulative(arguments, context):
        state = context.state(operation, CumulativeExtremum)
        return state.update(arguments[0], operation == MAXIMUM)
    return cumulative


def export_spread(state):
    retained = min(state.calls, state.horizon)
    return dict(count=state.calls, samples=[state.nodes[i][0] for i in range(state.capacity, state.capacity + retained)])


def restore_spread(value, budget, updates, horizon, base):
    require(type(value) is dict and value.keys() == {'count', 'samples'})
    count, samples = value['count'], value['samples']
    require(type(count) is int and 0 <= count <= min(updates, COUNT_LIMIT))
    require(type(samples) is list and len(samples) == min(count, horizon))
    if base == 'int':
        require(all(type(x) is int and -(2**63) <= x < 2**63 for x in samples))
    else:
        require(base == 'float' and all(type(x) is float and math.isfinite(x) for x in samples))
    state = SpreadWindow(budget, horizon)
    if count:
        state.initialize(count)
        for i, sample in enumerate(samples, state.capacity):
            state.nodes[i] = sample, sample
        state.rebuild(state.nodes, state.capacity)
        state.calls = count
    return state
