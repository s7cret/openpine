"""Shared Bollinger moments and Keltner EMA state."""

import copy
import math

from packages.contracts.channel_indicators import BBW, KCW, BOLLINGER, KELTNER
from . import EMAState, KernelError, SMASeedEMAState
from .dynamic_windows import (PrefixWindow, bound, export_window, finite, fork_window,
                              ratio, restore_window)
from .technical import true_range, units


def channel_result(basis, spread, multiplier, width):
    if spread is None or multiplier is None or basis is None:
        return None if width else (basis, None, None)
    if width and basis == 0:
        return None
    distance = finite(spread * multiplier)
    if width:
        return ratio(2 * units(distance), units(basis))
    return basis, finite(basis + distance), finite(basis - distance)


class KeltnerState:
    def __init__(self, budget, version):
        self.length = None
        self.parts = (
            tuple(SMASeedEMAState(budget=budget, warmup_label=f'{budget.label} Keltner {label}')
                  for label in ('basis', 'range')) if version == 4 else (EMAState(), EMAState())
        )

    def update(self, source, span, length):
        if self.length is not None and self.length != length:
            raise KernelError('CHANNEL_LENGTH_CHANGED', 'Keltner length must remain fixed.', 'rule.composite.length')
        self.length = length
        return self.parts[0].update(source, length), self.parts[1].update(span, length)


def kernel(operation):
    def run(arguments, context):
        horizon = context.history_capacity
        if operation in BOLLINGER:
            source, length, multiplier = arguments
            bound(length, horizon)
            state = context.state(operation, lambda: PrefixWindow(context.budget, 'window.moments', horizon))
            deviation = state.update(source, length, root=True)
            basis = state.mean(length)
            return channel_result(basis, deviation, multiplier, operation == BBW)

        high, low, previous_close, source, length, multiplier, *flags = arguments
        bound(length, horizon)
        use_true_range = not flags or flags[0] is True
        span = true_range(high, low, previous_close, False) if use_true_range else true_range(high, low, None, True)
        if span is not None:
            finite(span)
        state = context.state(operation, lambda: KeltnerState(context.budget, context.language_version))
        basis, average_span = state.update(source, span, length)
        return channel_result(basis, average_span, multiplier, operation == KCW)

    return run


def fork_keltner(state, budget):
    from packages.runtime.rollback import AppendOverlay

    clone = copy.copy(state)
    clone.parts = tuple(copy.copy(part) for part in state.parts)
    for target, original in zip(clone.parts, state.parts, strict=True):
        if type(original) is SMASeedEMAState:
            target.pending = AppendOverlay(original.pending)
            target.budget = budget
    return clone


def export_channel(operation, state):
    if operation in BOLLINGER:
        return export_window(state)[1]
    return dict(length=state.length, parts=[
        {'previous': part.previous, **({'pending': list(part.pending)} if type(part) is SMASeedEMAState else {})}
        for part in state.parts
    ])


def restore_channel(operation, value, budget, updates, horizon, version):
    if operation in BOLLINGER:
        return restore_window('window.moments', value, budget, updates, horizon)

    def require(condition):
        if not condition:
            raise ValueError('Invalid Keltner checkpoint.')

    require(operation in KELTNER and type(value) is dict and value.keys() == {'length', 'parts'})
    length, parts = value['length'], value['parts']
    require(updates >= 1 and type(length) is int and 1 <= length <= horizon)
    require(type(parts) is list and len(parts) == 2)
    state = KeltnerState(budget, version)
    state.length = length
    for index, (part, row) in enumerate(zip(state.parts, parts, strict=True)):
        require(type(row) is dict and row.keys() == ({'previous', 'pending'} if version == 4 else {'previous'}))
        previous = row['previous']
        require(previous is None or type(previous) is float and math.isfinite(previous) and (not index or previous >= 0))
        part.previous = previous
        if version == 4:
            pending = row['pending']
            require(type(pending) is list and len(pending) < length and len(pending) <= updates)
            require(previous is None or not pending and updates >= length)
            require(all(type(x) is float and math.isfinite(x) and (not index or x >= 0) for x in pending))
            part.pending = list(pending)
            part.reconcile()
    return state
