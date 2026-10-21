"""Bar-aligned histories and a shared bounded storage budget for one run."""

from dataclasses import fields, is_dataclass
from packages.contracts import HistoryIR, LoadIR, ReferenceIR
from packages.runtime.series import KernelError

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
    def __init__(self, limit=CELL_LIMIT):
        self.cells, self.limit = 0, limit

    def reserve(self):
        if self.cells >= self.limit:
            raise KernelError("HISTORY_STORAGE", "Run history storage limit exceeded.", "rule.history")
        self.cells += 1


class Ring:
    def __init__(self, budget, capacity=LIMIT + 1):
        self.budget, self.capacity = budget, capacity
        self.items, self.count = [], 0

    def append(self, value):
        if len(self.items) < self.capacity:
            self.budget.reserve()
            self.items.append(value)
        else:
            self.items[self.count % self.capacity] = value
        self.count += 1

    def ago(self, offset):
        if not 0 <= offset < min(self.count, self.capacity):
            return None
        return self.items[(self.count - offset - 1) % self.capacity]


class HistoryStore:
    def __init__(self, program, budget):
        self.sources, self.buffers = {}, {}

        def collect(value):
            if isinstance(value, HistoryIR):
                self.sources[value.series_id] = value.operand
            if is_dataclass(value):
                for field in fields(value):
                    collect(getattr(value, field.name))
            elif isinstance(value, tuple):
                for child in value:
                    collect(child)

        collect(program.statements)
        self.buffers = {key: Ring(budget) for key in self.sources}
        self.current = {}

    def read(self, node, offset, current, version):
        if type(offset) is not int or not 0 <= offset <= LIMIT:
            raise KernelError("HISTORY_OFFSET", "History offset must be a defined int in 0..100000.", "rule.history")
        self.current[node.series_id] = current
        value = current if offset == 0 else self.buffers[node.series_id].ago(offset - 1)
        return False if value is None and node.value_type.base == "bool" and version == 6 else value

    def commit(self, frame, values, strategy_values=None):
        for key, source in self.sources.items():
            if isinstance(source, ReferenceIR):
                value = (strategy_values or {}).get(source.contract.slot.removeprefix('strategy.')) if source.contract.slot.startswith('strategy.') else getattr(frame, source.contract.slot)
            elif isinstance(source, LoadIR):
                value = values.get(source.binding_id)
            else:
                value = self.current.get(key)
            self.buffers[key].append(value)
        self.current.clear()
