"""Bounded absolute deviations, Chande momentum, TSI and Williams %R."""

import copy

from packages.contracts.loops import CANCELLATION_INTERVAL
from packages.contracts.momentum import CMO, DEVIATIONS, DEV, LENGTH_INDEX, TSI, WPR
from . import EMAState, KernelError, SMASeedEMAState
from .dynamic_windows import PrefixWindow, bound, export_window, finite, fork_window, ratio, restore_window
from .technical import number, require, shape, units
from .window_indicators import STOCH, Stoch, export_indicator, fork_indicator, numeric, restore_indicator

SUM = 'window.sum'


def deviation(window, source, length, operation, charge_work):
    source = numeric(source)
    incoming = int(source is not None)
    result = None
    if window.count + incoming >= length and (incoming or operation == DEV):
        # Work against the proposed window before publishing the new prefix.
        ring = window.rings[0]
        retained = length - incoming
        newest = units(source) if incoming else 0
        total = ring.ago(0) - ring.ago(retained) + newest
        absolute = abs(length * newest - total) if incoming else 0
        if charge_work and incoming:
            charge_work(1)
        for start in range(0, retained, CANCELLATION_INTERVAL):
            stop = min(start + CANCELLATION_INTERVAL, retained)
            if charge_work:
                charge_work(stop - start)
            for i in range(start, stop):
                sample = ring.ago(i) - ring.ago(i + 1)
                absolute += abs(length * sample - total)
        if operation == DEV:
            result = ratio(absolute, (length * length) << 1074)
        elif absolute:
            result = ratio(200 * length * (length * newest - total), 3 * absolute)
    window.push(source, length)
    return result


class Chande:
    def __init__(self, budget, horizon):
        budget.preflight(2, 'exact')
        self.previous_source = None
        self.parts = tuple(PrefixWindow(budget, SUM, horizon) for _ in range(2))

    def update(self, source, length):
        source = numeric(source)
        delta = None if source is None or self.previous_source is None else finite(source - self.previous_source)
        gain, loss = self.parts
        if delta is not None:
            gain.budget.preflight(sum(part.count < part.horizon for part in self.parts), 'exact')
        gain.push(None if delta is None else max(0., delta), length)
        loss.push(None if delta is None else max(0., -delta), length)
        if source is not None:
            self.previous_source = source
        if gain.count < length:
            return None
        up, down = (part.rings[0].ago(0) - part.rings[0].ago(length) for part in self.parts)
        return ratio(100 * (up - down), up + down) if up or down else None


class TrueStrength:
    def __init__(self, budget, version):
        self.previous_source = None
        self.lengths = None
        self.parts = tuple(
            SMASeedEMAState(budget=budget, warmup_label=f'{budget.label} TSI {i}') if version < 5 else EMAState()
            for i in range(4)
        )

    def update(self, source, short, long):
        if self.lengths not in (None, (short, long)):
            raise KernelError('TSI_LENGTH_CHANGED', 'TSI periods must remain fixed.', 'rule.ema.length')
        source = numeric(source)
        delta = None if source is None or self.previous_source is None else finite(source - self.previous_source)
        self.lengths = short, long
        if source is not None:
            self.previous_source = source
        first, second, first_abs, second_abs = self.parts
        if delta is not None:
            value = first.update(delta, long)
            magnitude = first_abs.update(abs(delta), long)
            second.update(value, short)
            second_abs.update(magnitude, short)
        if not second_abs.previous:
            return None
        return ratio(units(second.previous), units(second_abs.previous))


def kernel(operation):
    def run(arguments, context):
        horizon = context.history_capacity
        if operation == TSI:
            for length in arguments[1:]:
                bound(length, horizon)
            state = context.state(TSI, lambda: TrueStrength(context.budget, context.language_version))
            return state.update(*arguments)
        length = arguments[LENGTH_INDEX[operation]]
        bound(length, horizon)
        if operation in DEVIATIONS:
            state = context.state(operation, lambda: PrefixWindow(context.budget, SUM, horizon))
            return deviation(state, *arguments, operation, context.charge_work)
        if operation == CMO:
            state = context.state(CMO, lambda: Chande(context.budget, horizon))
            return state.update(*arguments)
        high, low, close, _ = arguments
        state = context.state(WPR, lambda: Stoch(context.budget, horizon))
        return state.update(close, high, low, length, williams=True)

    return run


def fork_momentum(operation, state, budget):
    from packages.runtime.rollback import AppendOverlay

    if operation in DEVIATIONS:
        return fork_window(state, budget)
    if operation == WPR:
        return fork_indicator(state, budget)
    clone = copy.copy(state)
    if operation == CMO:
        clone.parts = tuple(fork_window(part, budget) for part in state.parts)
    else:
        clone.parts = tuple(copy.copy(part) for part in state.parts)
        for target, source in zip(clone.parts, state.parts, strict=True):
            if type(source) is SMASeedEMAState:
                target.pending = AppendOverlay(source.pending)
                target.budget = budget
    return clone


def export_momentum(operation, state):
    if operation in DEVIATIONS:
        return export_window(state)[1]
    if operation == WPR:
        return export_indicator(state)[1]
    if operation == CMO:
        return dict(previous_source=state.previous_source, parts=[export_window(part)[1] for part in state.parts])
    return dict(previous_source=state.previous_source, lengths=list(state.lengths), parts=[
        {'previous': part.previous, **({'pending': list(part.pending)} if type(part) is SMASeedEMAState else {})}
        for part in state.parts
    ])


def restore_momentum(operation, value, budget, updates, horizon, version):
    if operation in DEVIATIONS:
        return restore_window(SUM, value, budget, updates, horizon)
    if operation == WPR:
        return restore_indicator(STOCH, value, budget, updates, horizon)
    if operation == CMO:
        shape(value, ('previous_source', 'parts'))
        rows = value['parts']
        require(type(rows) is list and len(rows) == 2)
        state = object.__new__(Chande)
        state.previous_source = number(value['previous_source'])
        gain, loss = (restore_window(SUM, row, budget, updates, horizon) for row in rows)
        require((gain.calls, gain.count, gain.length) == (loss.calls, loss.count, loss.length))
        require(gain.count < gain.calls and (gain.count == 0 or state.previous_source is not None))
        require(all(x >= 0 for row in rows for key in ('samples', 'anchors', 'totals') for x in row[key]))
        require(all(not up or not down for up, down in zip(rows[0]['samples'], rows[1]['samples'], strict=True)))
        state.parts = gain, loss
        return state

    shape(value, ('previous_source', 'lengths', 'parts'))
    lengths, parts = value['lengths'], value['parts']
    require(type(lengths) is list and len(lengths) == 2 and
            all(type(n) is int and 1 <= n <= horizon for n in lengths))
    require(updates >= 1 and type(parts) is list and len(parts) == 4)
    state = TrueStrength(budget, version)
    state.lengths = tuple(lengths)
    state.previous_source = number(value['previous_source'])
    short, long = lengths
    for index, (part, row, period) in enumerate(zip(state.parts, parts, (long, short, long, short), strict=True)):
        shape(row, ('previous', 'pending') if version < 5 else ('previous',))
        part.previous = number(row['previous'])
        require(part.previous is None or state.previous_source is not None and updates >= (period + 1 if version < 5 else 2))
        if index >= 2:
            require(part.previous is None or part.previous >= 0)
        if version < 5:
            pending = row['pending']
            require(type(pending) is list and len(pending) < period and len(pending) < updates)
            require(part.previous is None or not pending)
            require(not pending or state.previous_source is not None)
            require(all(number(x) is not None and (index < 2 or x >= 0) for x in pending))
            part.pending = list(pending)
            part.reconcile()
    for first, second in ((0, 2), (1, 3)):
        a, b = state.parts[first], state.parts[second]
        require((a.previous is None) == (b.previous is None))
        if version < 5:
            require(len(a.pending) == len(b.pending))
    require(state.parts[0].previous is not None or (state.parts[1].previous is None and
            (version >= 5 or not state.parts[1].pending)))
    return state
