"""Disposable runtime branch with O(changed cells) history overlays.

Confirmed rings are read-only in a preview. Stateful kernels get private scalar
fields and append overlays, so an update never copies the complete bar history.
"""

import copy
import itertools

from packages.runtime.history import Ring
from packages.runtime.series import EMAState, SMASeedEMAState
from packages.runtime.series.basic import SMAState, ChangeState, CrossState
from packages.runtime.series.technical import TECHNICAL_TYPES, fork_technical
from packages.runtime.series.composite import COMPOSITE_TYPES,fork_composite


class RingOverlay:
    def __init__(self, base, budget):
        self.base, self.budget = base, budget
        self.count, self.capacity = base.count, base.capacity
        self.changed = {}

    def append(self, value):
        if self.count < self.capacity: self.budget.reserve()
        self.changed[self.count % self.capacity] = value
        self.count += 1

    def ago(self, offset):
        if not 0 <= offset < min(self.count, self.capacity): return None
        position = (self.count-offset-1) % self.capacity
        return self.changed[position] if position in self.changed else self.base.items[position]


class AppendOverlay:
    def __init__(self, base): self.base, self.tail = base, []
    def append(self, value): self.tail.append(value)
    def clear(self): self.base, self.tail = (), []
    def __len__(self): return len(self.base)+len(self.tail)
    def __iter__(self): return itertools.chain(self.base,self.tail)


def branch(evaluation, sink):
    result = copy.copy(evaluation)
    result.work = copy.copy(evaluation.work) if evaluation.work is not None else None
    result.budget = copy.copy(evaluation.budget)
    result.history = copy.copy(evaluation.history)
    result.history.current = {}
    result.history.buffers = {key:RingOverlay(ring,result.budget) for key,ring in evaluation.history.buffers.items()}
    result.persistent = dict(evaluation.persistent)
    result.pending_outputs, result.outputs, result.times, result.order_intents = [], {}, [], []
    result.output_sink = sink
    if evaluation.expression_sink is not None:result.expression_sink=sink
    result.states = {}
    for key, state in evaluation.states.items():
        if type(state) in COMPOSITE_TYPES:
            result.states[key]=fork_composite(state,result.budget)
            continue
        if type(state) in TECHNICAL_TYPES:
            result.states[key] = fork_technical(state,result.budget)
            continue
        if type(state) not in (EMAState,SMASeedEMAState,SMAState,ChangeState,CrossState):
            raise ValueError('Unregistered kernel cannot participate in rollback.')
        clone = copy.copy(state)
        if type(state) is SMASeedEMAState: clone.pending = AppendOverlay(state.pending)
        elif type(state) is SMAState: clone.prefixes = RingOverlay(state.prefixes,result.budget)
        elif type(state) is ChangeState: clone.values = RingOverlay(state.values,result.budget)
        result.states[key] = clone
    return result
