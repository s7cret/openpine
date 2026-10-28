"""Closed lifecycle for bounded technical kernels, private to each call/context.

RMA/ATR/RSI keep scalar state. Extrema use a fixed segment tree: each update
touches O(log length) cells, and preview branches overlay only those cells.
"""

import copy
import math

from packages.runtime.history import LIMIT
from packages.runtime.series import KernelError
from packages.runtime.series.basic import length_value


def fixed_length(state, length):
    length_value(length)
    if state.length is None:
        state.length = length
    elif state.length != length:
        raise KernelError("TECHNICAL_LENGTH_CHANGED", "Technical kernel length must remain constant per call and context.", "rule.technical.length")


def units(value):
    numerator, denominator = value.as_integer_ratio()
    return numerator << (1074 - (denominator.bit_length() - 1))


class RMAState:
    def __init__(self):
        self.length = None
        self.samples, self.total, self.previous = 0, 0, None

    def update(self, source, length):
        fixed_length(self, length)
        if source is None:
            return self.previous
        if not math.isfinite(source):
            raise KernelError("TECHNICAL_VALUE_RANGE", "Technical source exceeds the finite numeric range.", "rule.technical.numeric")
        if self.samples < length:
            self.total += units(source)
            self.samples += 1
            if self.samples < length:
                return None
            self.previous = self.total / (length << 1074)
            self.total = 0
        else:
            alpha = 1.0 / length
            self.previous = alpha * source + (1.0 - alpha) * self.previous
        return self.previous


def true_range(high, low, previous_close, handle_na):
    if high is None or low is None:
        return None
    if previous_close is None:
        return high - low if handle_na is True else None
    return max(high - low, abs(high - previous_close), abs(low - previous_close))


class ATRState(RMAState):
    def update(self, high, low, previous_close, length):
        return super().update(true_range(high, low, previous_close, True), length)


class RSIState:
    def __init__(self):
        self.length, self.previous_source = None, None
        self.gains, self.losses = RMAState(), RMAState()

    def value(self):
        gain, loss = self.gains.previous, self.losses.previous
        if gain is None or loss is None or gain == loss == 0:
            return None
        if loss == 0:
            return 100.0
        if gain == 0:
            return 0.0
        # Avoid overflow from gain/loss for finite, very different magnitudes.
        if gain >= loss:
            return 100.0 / (1.0 + loss / gain)
        ratio = gain / loss
        return 100.0 * ratio / (1.0 + ratio)

    def update(self, source, length):
        fixed_length(self, length)
        if source is not None:
            if self.previous_source is not None:
                delta = source - self.previous_source
                if not math.isfinite(delta):
                    raise KernelError("TECHNICAL_VALUE_RANGE", "RSI change exceeds the finite numeric range.", "rule.technical.numeric")
                self.gains.update(max(delta, 0.0), length)
                self.losses.update(max(-delta, 0.0), length)
            self.previous_source = source
        return self.value()


def combine(left, right, highest):
    if left is None:
        return right
    if right is None:
        return left
    return max(left, right) if highest else min(left, right)


class ExtremumState:
    def __init__(self, budget, highest):
        self.budget, self.highest = budget, highest
        self.length, self.count, self.nodes = None, 0, []

    def initialize(self, length):
        fixed_length(self, length)
        if not self.nodes:
            # Account every allocated slot before allocating the array.
            for _ in range(2 * length):
                self.budget.reserve()
            self.nodes = [None] * (2 * length)

    def update(self, source, length):
        self.initialize(length)
        position = length + self.count % length
        self.nodes[position] = source
        while position > 1:
            position //= 2
            self.nodes[position] = combine(self.nodes[2 * position], self.nodes[2 * position + 1], self.highest)
        self.count += 1
        return self.nodes[1]


class CellOverlay:
    """Immutable base plus changed indices; a branch never copies the tree."""
    def __init__(self, base, budget=None):
        self.base, self.changed = base, {}
        self.budget=budget

    def __len__(self):
        return len(self.base)

    def __getitem__(self, index):
        return self.changed[index] if index in self.changed else self.base[index]

    def __setitem__(self, index, value):
        if self.budget and index not in self.changed:self.budget.scratch(1)
        self.changed[index] = value


TECHNICAL_KINDS = ("rma", "atr", "rsi", "highest", "lowest")
TECHNICAL_TYPES = (RMAState, ATRState, RSIState, ExtremumState)


def export_technical(state):
    if type(state) in (RMAState, ATRState):
        return ("atr" if type(state) is ATRState else "rma"), {
            "length": state.length, "samples": state.samples, "total": state.total, "previous": state.previous}
    if type(state) is RSIState:
        return "rsi", {"length": state.length, "previous_source": state.previous_source,
            "gains": export_technical(state.gains)[1], "losses": export_technical(state.losses)[1]}
    if type(state) is ExtremumState:
        return ("highest" if state.highest else "lowest"), {"length": state.length, "count": state.count,
            "leaves": [state.nodes[index] for index in range(state.length, 2 * state.length)]}
    raise ValueError("Unregistered technical state.")


def require(condition):
    if not condition:
        raise ValueError("Invalid technical kernel checkpoint.")


def shape(value, names):
    require(type(value) is dict and value.keys() == set(names))


def number(value):
    require(value is None or type(value) is float and math.isfinite(value))
    return value


def length_value_or_none(value):
    require(value is None or type(value) is int and 1 <= value <= LIMIT)
    return value


def restore_rma(value, count, cls=RMAState, *, uninitialized=False):
    shape(value, ("length", "samples", "total", "previous"))
    length = length_value_or_none(value["length"])
    samples, total, previous = value["samples"], value["total"], number(value["previous"])
    require(type(samples) is int and 0 <= samples <= min(count, length or 0))
    require(type(total) is int and total.bit_length() <= 2115)
    if length is None:
        require(uninitialized and samples == total == 0 and previous is None)
    elif samples < length:
        require(previous is None and (samples != 0 or total == 0))
        require(abs(total) <= samples * units(float.fromhex("0x1.fffffffffffffp+1023")))
    else:
        require(previous is not None and total == 0)
    result = cls()
    result.length, result.samples, result.total, result.previous = length, samples, total, previous
    return result


def restore_technical(kind, value, budget, count):
    """Decode only known kinds, validating all nested data before publication."""
    require(kind in TECHNICAL_KINDS)
    if kind in ("rma", "atr"):
        result = restore_rma(value, count, ATRState if kind == "atr" else RMAState)
        if kind == "atr":
            require(result.total >= 0 and (result.previous is None or result.previous >= 0))
        return result
    if kind == "rsi":
        shape(value, ("length", "previous_source", "gains", "losses"))
        length = length_value_or_none(value["length"])
        require(length is not None)
        previous = number(value["previous_source"])
        gains = restore_rma(value["gains"], max(0, count - 1), uninitialized=True)
        losses = restore_rma(value["losses"], max(0, count - 1), uninitialized=True)
        require(gains.length == losses.length and gains.length in (None, length) and gains.samples == losses.samples)
        require((gains.previous is None) == (losses.previous is None))
        require(gains.total >= 0 and losses.total >= 0)
        require(gains.previous is None or gains.previous >= 0 and losses.previous >= 0)
        require(previous is not None or gains.length is None)
        result = RSIState()
        result.length, result.previous_source, result.gains, result.losses = length, previous, gains, losses
        return result
    shape(value, ("length", "count", "leaves"))
    length = length_value_or_none(value["length"])
    require(length is not None)
    n, leaves = value["count"], value["leaves"]
    require(type(n) is int and 1 <= n <= count)
    require(type(leaves) is list and len(leaves) == length)
    for index, leaf in enumerate(leaves):
        number(leaf)
        require(n >= length or index < n or leaf is None)
    result = ExtremumState(budget, kind == "highest")
    result.initialize(length)
    result.nodes[length:] = leaves
    for index in range(length - 1, 0, -1):
        result.nodes[index] = combine(result.nodes[2 * index], result.nodes[2 * index + 1], result.highest)
    result.count = n
    return result


def fork_technical(state, budget):
    require(type(state) in TECHNICAL_TYPES)
    result = copy.copy(state)
    if type(state) is RSIState:
        result.gains, result.losses = copy.copy(state.gains), copy.copy(state.losses)
    elif type(state) is ExtremumState:
        result.budget, result.nodes = budget, CellOverlay(state.nodes,budget)
    return result
