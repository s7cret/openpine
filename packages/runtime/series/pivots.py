"""Causal swing pivots over the reached samples of one written call."""

import operator

from packages.contracts.dynamic_windows import HORIZON
from packages.contracts.loops import CANCELLATION_INTERVAL
from packages.contracts.pivots import HIGH
from packages.runtime.history import Ring
from . import KernelError
from .dynamic_windows import COUNT_LIMIT


def leg(value):
    if value is None:
        return None
    if not 0 <= value <= HORIZON or value != int(value):
        raise KernelError('PIVOT_LENGTH', 'Pivot legs must be whole numbers in 0..100000.', 'rule.history')
    return int(value)


def candidate(ring, source, left, right, better, charge_work):
    def sample(offset):
        return ring.ago(offset - 1) if offset else source

    center = sample(right)
    if center is None:
        return None
    for start, end in ((0, right), (right + 1, left + right + 1)):
        for first in range(start, end, CANCELLATION_INTERVAL):
            stop = min(end, first + CANCELLATION_INTERVAL)
            charge_work(stop - first)
            for offset in range(first, stop):
                other = sample(offset)
                if other is None or not better(center, other):
                    return None
    return float(center)


def kernel(operation):
    better = operator.gt if operation == HIGH else operator.lt

    def run(arguments, context):
        source, left, right = arguments
        left, right = leg(left), leg(right)
        distance = left + right if left is not None and right is not None else None
        if distance is not None:
            if distance > HORIZON:
                raise KernelError('PIVOT_LENGTH', 'The sum of pivot legs exceeds 100000.', 'rule.history')
            if distance > context.history_capacity:
                raise KernelError('HISTORY_PLAN', 'Pivot exceeds its verified retention bound.', 'rule.history')
        ring = context.state(operation, lambda: Ring(context.budget, context.history_capacity))
        if ring.count == COUNT_LIMIT:
            raise KernelError('WINDOW_COUNT', 'Pivot call count exceeds int64.', 'rule.history')
        context.charge_work(1)
        result = (candidate(ring, source, left, right, better, context.charge_work)
                  if distance is not None and ring.count >= distance else None)
        ring.append(source)
        return result

    return run
