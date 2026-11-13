"""Bounded numeric order statistics over one ring per call and context."""

import copy
import math

from packages.contracts.loops import CANCELLATION_INTERVAL
from packages.contracts.window_statistics import LINEAR, MEDIAN, MODE, NEAREST, PERCENTILES, RANK, SKIP_MISSING
from packages.runtime.history import Ring
from packages.runtime.history_memory import Allocation
from . import KernelError
from .dynamic_windows import COUNT_LIMIT, bound, ratio
from .technical import require, shape, units

SCALE = 1 << 1074


def interpolate(left, right, part, denominator):
    return ratio((denominator - part) * units(left) + part * units(right), denominator * SCALE)


def sorted_value(operation, values, percentage):
    n = len(values)
    if operation == MEDIAN:
        right = values[n // 2]
        if n % 2:
            return right
        left = values[n // 2 - 1]
        if type(right) is int:
            total = left + right
            return total // 2 if total >= 0 else -(-total // 2)
        return interpolate(left, right, 1, 2)
    numerator, denominator = percentage.as_integer_ratio()
    denominator *= 100
    if operation == NEAREST:
        rank = (n * numerator + denominator - 1) // denominator
        return float(values[max(1, rank) - 1])
    i, fraction = divmod((n - 1) * numerator, denominator)
    return interpolate(values[i], values[i + 1], fraction, denominator) if fraction else float(values[i])


class OrderWindow:
    def __init__(self, budget, horizon):
        self.values = Ring(budget, horizon)
        self.calls = 0

    def update(self, operation, source, length, percentage, charge_work):
        ring = self.values
        bound(length, ring.capacity)
        if self.calls == COUNT_LIMIT:
            raise KernelError('WINDOW_COUNT', 'Statistics window call count exceeds int64.', 'rule.dynamic-window.count')
        if percentage is not None and not 0 <= percentage <= 100:
            raise KernelError('WINDOW_PERCENTAGE', 'Percentile must be in 0..100.', 'rule.dynamic-window.numeric')
        prior_only = operation == RANK
        append = source is not None or operation not in SKIP_MISSING
        incoming = int(append and not prior_only)
        ready = ring.count + incoming >= length
        if operation in PERCENTILES:
            ready = ready and percentage is not None
        if prior_only:
            ready = ready and source is not None
        result = self.calculate(operation, source, length - incoming, percentage, bool(incoming), charge_work) if ready else None
        if append:
            ring.append(source)
        self.calls += 1
        return result

    def calculate(self, operation, source, retained, percentage, include_source, charge_work):
        ring = self.values
        length = retained + include_source
        rank = operation == RANK
        # The native sort and its temporary list use the existing job budget.
        scratch = Allocation(ring.budget.allocation.account) if not rank and ring.budget.allocation else None
        values = [source] if include_source and operation != MODE else []
        counts = {source: 1} if operation == MODE and include_source else {}
        below = 0
        try:
            if scratch:
                scratch.reserve(3 * length if operation == MODE else 2 * length,
                                ring.label + ' statistics window', 'value')
            if charge_work and include_source:
                charge_work(1)
            if include_source and source is None:
                return None
            for start in range(0, retained, CANCELLATION_INTERVAL):
                stop = min(start + CANCELLATION_INTERVAL, retained)
                if charge_work:
                    charge_work(stop - start)
                for i in range(start, stop):
                    sample = ring.ago(i)
                    if sample is None:
                        return None
                    if rank:
                        below += sample <= source
                    elif operation == MODE:
                        counts[sample] = counts.get(sample, 0) + 1
                    else:
                        values.append(sample)
            if rank:
                return ratio(100 * below, retained)
            if operation == MODE:
                if charge_work:
                    charge_work(len(counts))
                return min(counts, key=lambda value: (-counts[value], value))
            if charge_work and length > 1:
                # Charge native sorting in units of eight estimated comparisons.
                charge_work((length * (length - 1).bit_length() + 7) // 8)
            values.sort()
            return sorted_value(operation, values, percentage)
        finally:
            if scratch:
                scratch.close()


def kernel(operation):
    def run(arguments, context):
        source, length, *percentages = arguments
        state = context.state(operation, lambda: OrderWindow(context.budget, context.history_capacity))
        return state.update(operation, source, length, percentages[0] if percentages else None, context.charge_work)
    return run


def fork_statistics(state, budget):
    from packages.runtime.rollback import RingOverlay

    clone = copy.copy(state)
    clone.values = RingOverlay(state.values, budget)
    return clone


def export_statistics(state):
    ring = state.values
    return dict(calls=state.calls, count=ring.count, capacity=ring.capacity, samples=list(ring.items))


def restore_statistics(operation, value, budget, updates, horizon, source_base):
    shape(value, ('calls', 'count', 'capacity', 'samples'))
    calls, count, cap, samples = (value[k] for k in ('calls', 'count', 'capacity', 'samples'))
    require(type(cap) is int and cap == horizon)
    require(type(calls) is int and type(count) is int and 0 <= count <= calls <= min(updates, COUNT_LIMIT) and calls >= 1)
    require(operation in SKIP_MISSING or count == calls)
    require(type(samples) is list and len(samples) == min(count, horizon))
    for sample in samples:
        valid = (type(sample) is int and -(2**63) <= sample < 2**63) if source_base == 'int' else (
            type(sample) is float and math.isfinite(sample))
        require(valid or (sample is None and operation not in SKIP_MISSING))
    state = OrderWindow(budget, horizon)
    budget.reserve(len(samples), 'value')
    state.values.items = list(samples)
    state.values.count = count
    state.calls = calls
    return state
