"""Fixed-minute candles derived from an immutable lower-timeframe snapshot."""
from contextlib import closing
from dataclasses import asdict
from math import fsum

from packages.contracts.calendar import timeframe
from packages.contracts.market_schedule import MarketSchedule


def bucket_page(source, target, start=0, limit=1000):
    lower = timeframe(source.identity.timeframe)
    target = timeframe(target)
    if not lower.isdigit() or not target.isdigit() or int(target) <= int(lower) or int(target) % int(lower):
        raise ValueError('Target must be a larger whole multiple of the source minute interval.')
    if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Use a nonnegative bucket offset and a limit from 1 to 1000.')
    step, width = int(lower)*60000, int(target)*60000
    meta = source.manifest['provenance']
    schedule = meta.get('market_schedule')
    if schedule is None:
        first, last = source.identity.first_ms, source.identity.last_ms
        intervals = [(first//width*width, (last//width+1)*width)]
    else:
        edges = MarketSchedule.from_payload(schedule, lower).boundaries
        intervals = zip(edges[::2], edges[1::2], strict=True)
    selection = meta.get('selection', {})
    total = 0
    bounds = []
    for opened, closed in intervals:
        lo = max(opened, selection.get('start_ms', opened))
        hi = min(closed, selection.get('end_ms', closed))
        if lo >= hi:
            continue
        first = (lo-opened)//width
        count = (hi-opened+width-1)//width-first
        for i in range(max(0, start-total), min(count, start+limit-total)):
            t = opened+(first+i)*width
            bounds.append((t, min(t+width, closed)))
        total += count
    return step, bounds, total


def candle_groups(source, bounds, step):
    if not bounds:
        return
    with closing(source.rows(bounds[0][0], bounds[-1][1])) as rows:
        row = next(rows, None)
        for start, end in bounds:
            bars = []
            while row is not None and row.time < end:
                if row.time < start or (row.time-start) % step or (bars and row.time <= bars[-1].time):
                    raise ValueError('Lower candle outside the pinned session grid.')
                bars.append(row)
                row = next(rows, None)
            yield start, end, bars


def aggregate_page(source, target, start=0, limit=1000):
    step, bounds, total = bucket_page(source, target, start, limit)
    candles = []
    for opened, closed, bars in candle_groups(source, bounds, step):
        expected = (closed-opened)//step
        fields = dict(open=None, high=None, low=None, close=None, volume=None)
        if len(bars) == expected:
            fields.update(open=bars[0].open, close=bars[-1].close)
            for field, operation in [('high', max), ('low', min), ('volume', fsum)]:
                values = [getattr(bar, field) for bar in bars]
                if None not in values:
                    try:
                        fields[field] = operation(values)
                    except OverflowError as error:
                        raise ValueError('Aggregate exceeds the finite numeric range.') from error
        candles.append(dict(start_ms=opened, end_ms=closed, observed=len(bars), expected=expected, **fields))
    return dict(schema_version='openpine.candle-aggregation.1', snapshot_id=source.manifest['snapshot_id'],
                symbol=source.identity.symbol, source_timeframe=source.identity.timeframe, timeframe=target,
                start=start, total=total, next=min(start+len(candles), total), candles=candles)


def intrabar_page(source, target, bucket, start=0, limit=1000):
    """Page actual observations within one parent; offsets count present bars."""
    step, bounds, total = bucket_page(source, target, bucket, 1)
    if not bounds:
        raise ValueError('Requested parent candle is outside the snapshot.')
    if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Use a nonnegative intrabar offset and a limit from 1 to 1000.')
    # A fixed-minute parent contains at most 1440 grid slots.
    with closing(candle_groups(source, bounds, step)) as groups:
        opened, closed, bars = next(groups)
    selected = bars[start:start+limit]
    return dict(schema_version='openpine.intrabar-page.1', snapshot_id=source.manifest['snapshot_id'],
                symbol=source.identity.symbol, source_timeframe=source.identity.timeframe, timeframe=target,
                bucket=bucket, total_buckets=total, start_ms=opened, end_ms=closed,
                expected=(closed-opened)//step, observed=len(bars), start=start,
                next=min(start+len(selected), len(bars)), bars=[asdict(bar) for bar in selected])
