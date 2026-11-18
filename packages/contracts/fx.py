"""Pinned daily FX observations. Days and publication instants use UTC milliseconds."""
from bisect import bisect_right
from math import isfinite
import re

from .corporate import fail, timestamp
from .wire import digest

DAY = 86_400_000


def code(value):
    return isinstance(value, str) and re.fullmatch('[A-Z]{3,4}', value) is not None


def normalize(data):
    if (type(data) is not dict or set(data) != {'schema_version', 'source', 'pairs'}
            or data['schema_version'] != 'openpine.fx.1' or not isinstance(data['source'], str)
            or not 0 < len(data['source']) <= 256 or type(data['pairs']) is not list
            or not 1 <= len(data['pairs']) <= 1024):
        fail('FX_SCHEMA', 'Expected a named snapshot of daily FX pairs.')
    pairs = {}
    for pair in data['pairs']:
        if type(pair) is not dict or set(pair) != {'base', 'quote', 'start_ms', 'end_ms', 'rates'}:
            fail('FX_SCHEMA', 'Invalid currency pair fields.')
        a, b = pair['base'], pair['quote']
        start, end = pair['start_ms'], pair['end_ms']
        if (not code(a) or not code(b) or a == b or (a,b) in pairs or not timestamp(start)
                or not timestamp(end) or end <= start or type(pair['rates']) is not list):
            fail('FX_SCHEMA', 'Invalid pair, coverage or duplicate stream.')
        rows = {}
        for row in pair['rates']:
            if type(row) is not dict or set(row) != {'day_ms','available_ms','rate'}:
                fail('FX_SCHEMA', 'Invalid daily FX quote fields.')
            day, at, rate = row['day_ms'], row['available_ms'], row['rate']
            if (not timestamp(day) or day % DAY or not timestamp(at) or at < day
                    or at >= end or type(rate) not in (int,float) or not 0 < rate <= 1e308
                    or not isfinite(1. / rate)):
                fail('FX_VALUE', 'Expected a positive finite invertible daily rate.')
            key = day, at
            if key in rows and rows[key]['rate'] != rate:
                fail('FX_CONFLICT', 'Conflicting FX quotes for one pair, day and publication.')
            rows[key] = {**row, 'rate':float(rate)}
        pairs[a,b] = {**pair, 'rates':[rows[key] for key in sorted(rows)]}
    return {**data, 'pairs':[pairs[key] for key in sorted(pairs)]}


class Rates:
    def __init__(self, data):
        snapshot = normalize(data)
        self.sha256 = digest(snapshot)
        self.pairs = {}
        self.codes = set()
        for pair in snapshot['pairs']:
            times, values = [], []
            latest = -1
            for row in sorted(pair['rates'], key=lambda r: (max(r['day_ms']+DAY,r['available_ms']),r['day_ms'],r['available_ms'])):
                day = row['day_ms']
                if day >= latest:
                    latest = day
                    times.append(max(day+DAY,row['available_ms']))
                    values.append(row['rate'])
            key = pair['base'], pair['quote']
            self.codes.update(key)
            self.pairs[key] = pair['start_ms'], pair['end_ms'], times, values

    def bind(self, a, b, ignore=False, coverage=None):
        if a not in self.codes or b not in self.codes:
            if ignore:
                return lambda at: None
            fail('FX_CURRENCY', 'Currency is unknown in the pinned snapshot.')
        if a == b:
            return lambda at: 1.
        inverse = (a,b) not in self.pairs
        pair = self.pairs.get((b,a) if inverse else (a,b))
        if pair is None:
            if ignore:
                return lambda at: None
            fail('FX_PAIR', 'No direct or inverse stream for the requested currencies.')
        start, end, times, values = pair
        if coverage and (coverage[0] < start or coverage[1] > end):
            fail('FX_COVERAGE', 'Requested FX interval is outside declared coverage.')
        def rate(at):
            if not start <= at < end:
                fail('FX_COVERAGE', 'FX observation is outside declared coverage.')
            pos = bisect_right(times,at)-1
            if pos < 0:
                if ignore:
                    return None
                fail('FX_UNAVAILABLE', 'No completed daily quote was available at this observation.')
            return 1. / values[pos] if inverse else values[pos]
        return rate
