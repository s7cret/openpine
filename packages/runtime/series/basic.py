"""Bounded SMA, change and crossing states; each written call owns one instance."""

from packages.runtime.history import LIMIT, Ring
from packages.runtime.series import KernelError


def length_value(length, *, zero=False):
    if type(length) is not int or not (0 if zero else 1) <= length <= LIMIT:
        raise KernelError("SERIES_LENGTH", f"Length must be a defined int in {0 if zero else 1}..{LIMIT}.", "rule.series.length")


class SMAState:
    """Exact binary-float sums in units of 2**-1074 avoid overflow/cancellation.

    A prefix ring makes changing window lengths O(1) without losing old samples.
    Only the final mean is rounded to a Python float.
    """

    def __init__(self, budget):
        self.prefixes = Ring(budget)
        self.prefixes.append(0)
        self.total, self.count = 0, 0

    def update(self, source, length):
        length_value(length)
        if source is not None:
            numerator, denominator = source.as_integer_ratio()
            self.total += numerator << (1074 - (denominator.bit_length() - 1))
            self.prefixes.append(self.total)
            self.count += 1
        if self.count < length:
            return None
        previous = self.prefixes.ago(length)
        return (self.total - previous) / (length << 1074)


class ChangeState:
    def __init__(self, budget):
        self.values = Ring(budget)

    def update(self, source, length, version):
        length_value(length, zero=True)
        previous = source if length == 0 else self.values.ago(length - 1)
        self.values.append(source)
        if source is None or previous is None:
            return False if type(source) is bool and version == 6 else None
        return source != previous if type(source) is bool else source - previous


class CrossState:
    def __init__(self):
        self.previous = None

    def update(self, left, right, direction):
        previous = self.previous
        self.previous = (left, right)
        if previous is None or any(v is None for v in (left, right, *previous)):
            return False
        up = left > right and previous[0] <= previous[1]
        down = left < right and previous[0] >= previous[1]
        return up if direction == "over" else down if direction == "under" else up or down
