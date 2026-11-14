"""Exact moving Pearson moments and budgeted rank correlation."""

import copy
import math

from packages.contracts.correlation import MOMENTS, PEARSON, RCI
from packages.contracts.loops import CANCELLATION_INTERVAL
from packages.contracts.memory import charge
from packages.runtime.history import Ring
from packages.runtime.history_memory import Allocation
from . import KernelError
from .dynamic_windows import COUNT_LIMIT, bound
from .technical import require, shape, units


SCALE = 1 << 1074
MAX_FLOAT_UNIT = units(float.fromhex('0x1.fffffffffffffp+1023'))
INT_MIN, INT_MAX = -(2**63), 2**63 - 1


def moments(x, y):
    return x, y, x * x, y * y, x * y


def coefficient(n, x, y, xx, yy, xy):
    denominator = (n * xx - x * x) * (n * yy - y * y)
    if not denominator:
        return None
    covariance = n * xy - x * y
    if not covariance:
        return 0.
    numerator = covariance * covariance
    if numerator == denominator:
        return -1. if covariance < 0 else 1.
    exponent = (numerator.bit_length() - denominator.bit_length()) // 2
    # Scale the squared ratio before sqrt, so a representable correlation
    # does not underflow just because its square is below float range.
    normalized = (numerator << (-2 * exponent)) / denominator
    return math.copysign(math.ldexp(math.sqrt(normalized), exponent), -1. if covariance < 0 else 1.)


class PearsonWindow:
    def __init__(self, budget, horizon):
        self.budget, self.horizon = budget, horizon
        self.calls = 0
        self.rings = [Ring(budget, horizon + 1, 'exact') for _ in range(MOMENTS)]
        budget.preflight(MOMENTS, 'exact')
        for ring in self.rings:
            ring.append(0)

    def update(self, source1, source2, length, charge_work):
        bound(length, self.horizon)
        if self.calls == COUNT_LIMIT:
            raise KernelError('WINDOW_COUNT', 'Correlation call count exceeds int64.', 'rule.history')
        charge_work(1)
        if source1 is not None and source2 is not None:
            values = moments(units(source1), units(source2))
            count = self.rings[0].count
            growth = MOMENTS if count <= self.horizon else 0
            self.budget.preflight(growth, 'exact')
            if self.budget.temporary:
                from packages.runtime.rollback import RingOverlay

                scratch = sum(type(ring) is RingOverlay and count % ring.capacity not in ring.changed
                              for ring in self.rings)
                self.budget.temporary.account.check(charge('exact', growth + scratch) + charge('preview', scratch),
                                                    self.budget.label, 'correlation')
            for ring, value in zip(self.rings, values, strict=True):
                ring.append(ring.ago(0) + value)
        self.calls += 1
        if length < self.rings[0].count:
            return coefficient(length, *(ring.ago(0) - ring.ago(length) for ring in self.rings))
        return None


class RankWindow:
    def __init__(self, budget, horizon):
        self.values = Ring(budget, horizon)

    def update(self, source, length, charge_work):
        ring = self.values
        bound(length, ring.capacity)
        if ring.count == COUNT_LIMIT:
            raise KernelError('WINDOW_COUNT', 'Rank correlation call count exceeds int64.', 'rule.history')
        charge_work(1)
        result = self.ranked(source, length, charge_work) if source is not None and 1 < length <= ring.count + 1 else None
        ring.append(source)
        return result

    def ranked(self, source, length, charge_work):
        ring = self.values
        scratch = Allocation(ring.budget.allocation.account) if ring.budget.allocation else None
        try:
            if scratch:
                scratch.reserve(3 * length, ring.label + ' rank correlation', 'value')
            ordered = [(source, length - 1)]
            for start in range(0, length - 1, CANCELLATION_INTERVAL):
                end = min(length - 1, start + CANCELLATION_INTERVAL)
                charge_work(end - start)
                for index in range(start, end):
                    sample = ring.ago(index)
                    if sample is None:
                        return None
                    ordered.append((sample, length - 2 - index))
            charge_work((length * (length - 1).bit_length() + 7) // 8)
            ordered.sort()
            # Doubled ranks preserve exact half-ranks for ties.
            rank_sum = rank_square = cross = 0
            first = time_total = 0
            for last, (price, time) in enumerate(ordered, 1):
                if (last - 1) % CANCELLATION_INTERVAL == 0:
                    charge_work(min(CANCELLATION_INTERVAL, length - last + 1))
                time_total += time
                if last < length and ordered[last][0] == price:
                    continue
                rank = first + last - 1
                group = last - first
                rank_sum += group * rank
                rank_square += group * rank * rank
                cross += time_total * rank
                first, time_total = last, 0
            time_sum = length * (length - 1) // 2
            time_square = time_sum * (2 * length - 1) // 3
            result = coefficient(length, time_sum, rank_sum, time_square, rank_square, cross)
            return None if result is None else result * 100
        finally:
            if scratch:
                scratch.close()


def kernel(operation):
    window = PearsonWindow if operation == PEARSON else RankWindow

    def run(arguments, context):
        bound(arguments[-1], context.history_capacity)
        return context.state(operation, lambda: window(context.budget, context.history_capacity)).update(
            *arguments, context.charge_work)

    return run


def fork_rank(state, budget):
    from packages.runtime.rollback import RingOverlay

    clone = copy.copy(state)
    clone.values = RingOverlay(state.values, budget)
    return clone


def export_pearson(state):
    rings = state.rings
    count = rings[0].count - 1
    retained = min(count, state.horizon)
    # Flat source columns keep the checkpoint decoder from allocating one
    # container per retained pair. Their shared positions preserve pairing.
    samples = [[compact_sample(ring.ago(i) - ring.ago(i + 1))
                for i in range(retained - 1, -1, -1)] for ring in rings[:2]]
    return dict(calls=state.calls, count=count, retention=state.horizon, samples=samples,
                anchor=[ring.ago(retained) for ring in rings], totals=[ring.ago(0) for ring in rings])


def compact_sample(value):
    whole, fraction = divmod(value, SCALE)
    return whole if not fraction and INT_MIN <= whole <= INT_MAX else value / SCALE


def sample_unit(value, base):
    if type(value) is int:
        require(INT_MIN <= value <= INT_MAX)
        require(base == 'int' or int(float(value)) == value)
    else:
        require(base == 'float' and type(value) is float and math.isfinite(value))
    result = units(value)
    canonical = compact_sample(result)
    require(type(canonical) is type(value) and canonical == value)
    return result


def restore_pearson(value, budget, updates, horizon, bases):
    shape(value, ('calls', 'count', 'retention', 'samples', 'anchor', 'totals'))
    calls, count, columns = value['calls'], value['count'], value['samples']
    require(type(value['retention']) is int and value['retention'] == horizon)
    require(type(calls) is int and type(count) is int and 0 <= count <= calls <= min(updates, COUNT_LIMIT) and calls > 0)
    retained = min(count, horizon)
    require(type(columns) is list and len(columns) == 2)
    require(all(type(column) is list and len(column) == retained for column in columns))
    anchor, totals = value['anchor'], value['totals']
    before = count - retained
    for prefix, samples in ((anchor, before), (totals, count)):
        require(type(prefix) is list and len(prefix) == MOMENTS and all(type(v) is int for v in prefix))
        x, y, xx, yy, xy = prefix
        require(abs(x) <= samples * MAX_FLOAT_UNIT and abs(y) <= samples * MAX_FLOAT_UNIT and
                0 <= xx <= samples * MAX_FLOAT_UNIT**2 and 0 <= yy <= samples * MAX_FLOAT_UNIT**2 and
                abs(xy) <= samples * MAX_FLOAT_UNIT**2)
        a, b, c = samples * xx - x*x, samples * yy - y*y, samples * xy - x*y
        require(a >= 0 and b >= 0 and c*c <= a*b)
    budget.preflight(MOMENTS * (retained + 1), 'exact')
    state = PearsonWindow(budget, horizon)
    state.calls = calls
    current = anchor
    budget.reserve(MOMENTS * retained, 'exact')
    for ring, first in zip(state.rings, anchor, strict=True):
        ring.items = [None] * (retained + 1)
        ring.items[before % ring.capacity] = first
        ring.count = count + 1
    for index, (x, y) in enumerate(zip(*columns, strict=True), before + 1):
        deltas = moments(sample_unit(x, bases[0]), sample_unit(y, bases[1]))
        current = [a + b for a, b in zip(current, deltas, strict=True)]
        for ring, partial in zip(state.rings, current, strict=True):
            ring.items[index % ring.capacity] = partial
    require(current == totals)
    return state
