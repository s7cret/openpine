"""Disposable runtime branch with O(changed cells) history overlays.

Confirmed rings are read-only in a preview. Stateful kernels get private scalar
fields and append overlays, so an update never copies the complete bar history.
"""

import copy
import itertools

from packages.runtime.series.signals import SIGNAL_TYPES,fork_signal
from packages.runtime.history import Ring
from packages.runtime.series import EMAState, SMASeedEMAState
from packages.runtime.series.basic import SMAState, ChangeState, CrossState
from packages.runtime.series.technical import TECHNICAL_TYPES, fork_technical
from packages.runtime.series.composite import COMPOSITE_TYPES,fork_composite
from packages.runtime.series.dynamic_windows import TYPES as WINDOW_TYPES,fork_window
from packages.runtime.series.dynamic_signals import TYPES as SW_TYPES,fork_window as fork_sw


class RingOverlay:
    def __init__(self, base, budget, heap=None):
        self.base, self.budget = base, budget
        self.heap = heap
        self.count, self.capacity = base.count, base.capacity
        self.kind,self.label=base.kind,base.label+' preview'
        self.string_bytes=base.string_bytes
        self.changed = {}

    def append(self, value):
        if not self.capacity:self.count+=1;return
        slot_kind='string_slot' if self.kind=='string' else self.kind
        if self.count < self.capacity: self.budget.reserve(kind=slot_kind,label=self.label)
        index = self.count % self.capacity
        # Exact prefix integers plus a dict entry can exceed one cell charge.
        if index not in self.changed:
            self.budget.scratch(1,slot_kind,self.label,temporary=True);self.budget.scratch(1,'preview',self.label)
        old = self.changed[index] if index in self.changed else self.base.items[index] if self.count >= self.capacity else None
        if self.kind=='string':
            from packages.contracts.memory import string_payload
            if self.budget.temporary:self.budget.temporary.resize(string_payload(value),self.label+' string '+str(index),'byte')
            self.string_bytes+=string_payload(value)-string_payload(old)
        self.changed[index] = value
        if self.heap:
            self.heap.retain(value);self.heap.release(old)
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
    result.is_preview = True
    result.observe(evaluation.observer)
    if result.observer:result.observation_branch=result.observer.fork(evaluation)
    result.work = copy.copy(evaluation.work) if evaluation.work is not None else None
    result.budget = copy.copy(evaluation.budget)
    result.heap = evaluation.heap.fork() if evaluation.heap is not None else None
    result.history = copy.copy(evaluation.history)
    result.history.current = {}
    from packages.runtime.history import ArrayRing
    result.history.buffers = {key:RingOverlay(ring,result.budget,result.heap if isinstance(ring,ArrayRing) else None) for key,ring in evaluation.history.buffers.items()}
    result.persistent = dict(evaluation.persistent)
    result.pending_outputs, result.outputs, result.times, result.order_intents = [], {}, [], []
    result.output_sink = sink
    if evaluation.expression_sink is not None:result.expression_sink=sink
    result.states = {}
    for key, state in evaluation.states.items():
        if type(state) in SW_TYPES:
            result.states[key]=fork_sw(state,result.budget)
            continue
        if type(state) in WINDOW_TYPES:
            result.states[key]=fork_window(state,result.budget)
            continue
        if type(state) in SIGNAL_TYPES:
            result.states[key]=fork_signal(state,result.budget)
            continue
        if type(state) in COMPOSITE_TYPES:
            result.states[key]=fork_composite(state,result.budget)
            continue
        if type(state) in TECHNICAL_TYPES:
            result.states[key] = fork_technical(state,result.budget)
            continue
        if type(state) not in (EMAState,SMASeedEMAState,SMAState,ChangeState,CrossState):
            raise ValueError('Unregistered kernel cannot participate in rollback.')
        clone = copy.copy(state)
        if type(state) is SMASeedEMAState: clone.pending = AppendOverlay(state.pending);clone.budget=result.budget
        elif type(state) is SMAState: clone.prefixes = RingOverlay(state.prefixes,result.budget)
        elif type(state) is ChangeState: clone.values = RingOverlay(state.values,result.budget)
        result.states[key] = clone
    return result
