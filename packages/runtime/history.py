"""Bar-aligned histories and a shared bounded storage budget for one run."""

from dataclasses import fields, is_dataclass
from packages.contracts import HistoryIR, LoadIR, ReferenceIR
from packages.runtime.series import KernelError
from packages.contracts.arrays import ARRAY_BASES

LIMIT = 100000
CELL_LIMIT = 1000000


def series_policy(version):
    return {"history_limit": LIMIT, "history_cells": CELL_LIMIT,
        "missing_bool": "false" if version == 6 else "na", "history_alignment": "bars",
        "skipped_expression": "hole", "variable_history": "end_of_bar",
        "skipped_call": "no_update", "sma_na": "last_length_non_na", "change_na": "include",
        "cross_missing": "false", "modulo": "floor", "round_ties": "positive_infinity",
        "round_precision": [-15, 15], "call_history_limit": LIMIT, "min_max_arity": [2, 32]}


class StorageBudget:
    def __init__(self, limit=CELL_LIMIT, account=None):
        from .history_memory import Allocation
        self._cells, self.limit = 0, limit
        self.allocation=Allocation(account) if account is not None else None
        self.temporary=Allocation(account) if account is not None else None
        self.label='history';self.kind='value'

    @property
    def cells(self):return self._cells

    @cells.setter
    def cells(self,n):
        if self.allocation and n>self._cells:self.allocation.reserve(n-self._cells,self.label,self.kind)
        self._cells=n

    def __copy__(self):
        result=StorageBudget(self.limit,self.allocation.account if self.allocation else None)
        result._cells=self.cells;result.label=self.label+' preview';result.kind=self.kind
        return result

    def scratch(self,n,kind='preview',label=None,*,temporary=False):
        allocation=self.temporary if temporary or kind=='preview' else self.allocation
        if allocation:allocation.reserve(n,label or self.label+' scratch',kind)

    def close(self):
        if self.allocation:self.allocation.close()
        if self.temporary:self.temporary.close()

    def preflight_many(self,parts):
        self.preflight(sum(n for kind,n in parts),'index')
        if self.allocation:
            from packages.contracts.memory import charge
            self.allocation.account.check(sum(charge(kind,n) for kind,n in parts),self.label,'mixed')

    def reserve_many(self,parts):
        self.preflight_many(parts)
        for kind,n in parts:self.reserve(n,kind)

    def preflight(self,n,kind=None):
        if self.cells+n>self.limit:
            raise KernelError('HISTORY_STORAGE',f'Run history storage limit exceeded at {self.label}.','rule.history')
        if self.allocation:
            from packages.contracts.memory import charge
            a=self.allocation.account
            a.check(charge(kind or self.kind,n),self.label,kind or self.kind)

    def reserve(self,n=1,kind=None,label=None):
        previous=self.label
        if label is not None:self.label=label
        try:
            self.preflight(n,kind)
            if self.allocation:self.allocation.reserve(n,self.label,kind or self.kind)
            self._cells+=n
        finally:self.label=previous

    def reserve_logical(self,n):
        """Restore a spent lifetime quota whose objects no longer exist."""
        if type(n) is not int or n<0:raise ValueError('Invalid logical cell count.')
        if self.cells+n>self.limit:
            raise KernelError('HISTORY_STORAGE',f'Run history storage limit exceeded at {self.label}.','rule.history')
        self._cells+=n


class Ring:
    def __init__(self, budget, capacity=LIMIT + 1, kind='value', label=None):
        self.budget, self.capacity = budget, capacity
        self.kind,self.label=kind,label or budget.label
        self.items, self.count = [], 0
        self.string_bytes=0;self.payload_label=None

    def reconcile(self):
        if self.kind=='string' and self.budget.allocation and self.payload_label:
            self.budget.allocation.resize(self.string_bytes,self.payload_label,'byte')

    def append(self, value):
        if not self.capacity:self.count+=1;return
        kind=self.kind
        if kind=='string':
            from packages.contracts.memory import string_payload,charge
            old=self.items[self.count%self.capacity] if len(self.items)==self.capacity else None
            size=self.string_bytes+string_payload(value)-string_payload(old)
            growth=len(self.items)<self.capacity
            if growth:self.budget.preflight(1,'string_slot')
            if self.budget.allocation:
                self.budget.allocation.account.check(max(0,size-self.string_bytes)+(charge('string_slot') if growth else 0),self.label,'string')
            self.string_bytes=size
            if self.payload_label is None:self.payload_label=self.label+' string payload'
            self.reconcile();kind='string_slot'
        if len(self.items) < self.capacity:
            self.budget.reserve(kind=kind,label=self.label)
            self.items.append(value)
        else:
            self.items[self.count % self.capacity] = value
        self.count += 1

    def ago(self, offset):
        if not 0 <= offset < min(self.count, self.capacity):
            return None
        return self.items[(self.count - offset - 1) % self.capacity]


class ArrayRing(Ring):
    def __init__(self, budget, heap, capacity):
        super().__init__(budget, capacity,'reference')
        self.heap = heap

    def append(self, value):
        if not self.capacity:self.count+=1;return
        old = self.items[self.count % self.capacity] if len(self.items) == self.capacity else None
        super().append(value)
        self.heap.retain(value)
        self.heap.release(old)


class HistoryStore:
    def __init__(self, program, budget, heap=None, storage_plan=None):
        self.version=program.identity.language_version
        self.sources, self.buffers = {}, {}
        self.capacities = {r['series_id']:r['retention'] for r in (storage_plan or {}).get('histories',[])}

        def collect(value):
            # Request arguments execute in their own private runtime. They must
            # never create histories on primary bars or consume its cell budget.
            if getattr(value,'operation',None) in ('context.request','context.lower_tf'):return
            if isinstance(value, HistoryIR):
                self.sources[value.series_id] = value.operand
                if value.series_id not in self.capacities:
                    from packages.contracts import LiteralIR
                    size = max(0, value.offset.value) if isinstance(value.offset, LiteralIR) else LIMIT
                    self.capacities[value.series_id] = max(size, self.capacities.get(value.series_id, 0))
            if is_dataclass(value):
                for field in fields(value):
                    collect(getattr(value, field.name))
            elif isinstance(value, tuple):
                for child in value:
                    collect(child)

        collect(program.statements)
        from packages.contracts.memory import value_kind
        self.buffers = {key: ArrayRing(budget, heap, self.capacities[key]) if self.sources[key].value_type.base in ARRAY_BASES
            else Ring(budget,self.capacities[key],value_kind(self.sources[key].value_type.base),'history '+key) for key in self.sources}
        self.current = {}

    def read(self, node, offset, current, version):
        if type(offset) is not int or not 0 <= offset <= LIMIT:
            raise KernelError("HISTORY_OFFSET", "History offset must be a defined int in 0..100000.", "rule.history")
        self.current[node.series_id] = current
        value = current if offset == 0 else self.buffers[node.series_id].ago(offset - 1)
        return False if value is None and node.value_type.base == "bool" and version == 6 else value

    def commit(self, frame, values, strategy_values=None,reference_value=None):
        for key, source in self.sources.items():
            if isinstance(source, ReferenceIR):
                value = reference_value(source) if source.contract.slot.startswith('environment.') else (strategy_values or {}).get(source.contract.slot.removeprefix('strategy.')) if source.contract.slot.startswith('strategy.') else getattr(frame, source.contract.slot)
            elif isinstance(source, LoadIR):
                value = values.get(source.binding_id)
            else:
                value = self.current.get(key)
            ring=self.buffers[key];ring.label=getattr(self,'owner','primary')+' / history '+key+' / '+frame.context_id
            try:ring.append(value)
            except KernelError as error:
                from packages.contracts import Diagnostic,OpenPineError
                raise OpenPineError(Diagnostic('RUNTIME_ERROR',error.code,str(error),source.span,error.rule_id,self.version)) from error
        self.current.clear()
