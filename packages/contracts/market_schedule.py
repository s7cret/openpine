"""Finite, immutable exchange opening intervals on a snapshot's UTC candle grid."""

from bisect import bisect_right
from dataclasses import dataclass

from .calendar import aligned


@dataclass(frozen=True)
class MarketSchedule:
    start_ms: int
    end_ms: int
    period_ms: int
    boundaries: tuple[int, ...]

    @classmethod
    def from_payload(cls, value, timeframe):
        fields = {'schema_version', 'start_ms', 'end_ms', 'open_intervals'}
        if (type(value) is not dict or value.keys() != fields
                or value['schema_version'] != 'openpine.market-schedule.1'):
            raise ValueError('Invalid market schedule.')
        start, end = value['start_ms'], value['end_ms']
        if not (timeframe.isdigit() and aligned(start, timeframe) and aligned(end, timeframe) and start < end):
            raise ValueError('Market schedule requires a bounded range aligned to minute candles.')
        intervals = value['open_intervals']
        if type(intervals) is not list:
            raise ValueError('Market schedule requires a list of open intervals.')
        edges = []
        for interval in intervals:
            if type(interval) is not list or len(interval) != 2:
                raise ValueError('Invalid open market interval.')
            a, b = interval
            if not (aligned(a, timeframe) and aligned(b, timeframe) and start <= a < b <= end):
                raise ValueError('Open market interval outside schedule or candle grid.')
            if edges and a < edges[-1]:
                raise ValueError('Market intervals must be ordered and disjoint.')
            if edges and a == edges[-1]:
                edges[-1] = b
            else:
                edges.extend((a, b))
        return cls(start, end, int(timeframe)*60000, tuple(edges))

    def require_open(self, timestamp):
        if type(timestamp) is not int or timestamp % self.period_ms:
            raise ValueError('Candle opening time outside scheduled candle grid.')
        if not (self.start_ms <= timestamp < self.end_ms and bisect_right(self.boundaries, timestamp) % 2):
            raise ValueError('Candle outside scheduled market hours.')

    def segments(self, start, stop):
        index = bisect_right(self.boundaries, start)
        while start < stop:
            end = min(stop, self.boundaries[index]) if index < len(self.boundaries) else stop
            yield start, end, bool(index % 2)
            start, index = end, index + 1
