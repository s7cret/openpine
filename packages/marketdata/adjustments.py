"""As-of views of pinned candles under OpenPine's additive cash profile."""
from bisect import bisect_right
from contextlib import closing
from dataclasses import asdict
from math import isfinite

from packages.contracts.corporate import ActionIndex


def _factors(index, source, cutoff):
    symbol = source.identity.symbol
    currency = source.manifest['provenance'].get('environment', {}).get('instrument', {}).get('currency')
    events = []
    for family in ('splits', 'dividends'):
        stream, _, rows = index.select(symbol, family, False, currency)
        if stream['start_ms'] > source.identity.first_ms or stream['end_ms'] <= cutoff:
            raise ValueError('Corporate coverage must include the candle history through the as-of time.')
        for row in rows:
            if source.identity.first_ms < row['effective_ms'] <= cutoff and row['available_ms'] <= cutoff:
                if family == 'dividends' and not currency:
                    raise ValueError('Cash adjustment requires the pinned instrument currency.')
                events.append((row['effective_ms'], family, row))
    # Descending time. At a shared instant the gross dividend is quoted per new share.
    events.sort(key=lambda event: (event[0], event[1] == 'dividends'), reverse=True)
    times, suffixes = [], []
    scale, cash = 1., 0.
    for effective, family, row in events:
        if family == 'splits':
            scale *= row['denominator'] / row['numerator']
        else:
            cash += row['gross'] * scale
        if not isfinite(scale) or scale <= 0 or not isfinite(cash):
            raise ValueError('Corporate adjustment exceeds numeric range.')
        times.append(effective)
        suffixes.append((scale, cash))
    return times[::-1], [*reversed(suffixes), (1., 0.)]


def price_page(source, profile, actions=None, as_of=None, start=0, limit=1000):
    cutoff = source.identity.last_ms if as_of is None else as_of
    if type(cutoff) is not int or not 0 <= cutoff < 2**63:
        raise ValueError('Invalid as-of timestamp.')
    if type(start) is not int or start < 0 or type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError('Use a nonnegative candle offset and limit 1..1000.')
    if profile not in ('raw', 'split-cash') or (profile == 'split-cash') != (actions is not None):
        raise ValueError('The split-cash profile requires a corporate dataset; raw takes candles alone.')
    index = ActionIndex(actions) if actions is not None else None
    times, factors = _factors(index, source, cutoff) if index else ([], [(1., 0.)])
    with closing(source.rows(0, cutoff + 1, offset=start, limit=limit+1)) as rows:
        page = list(rows)
    result = []
    for bar in page[:limit]:
        values = asdict(bar)
        if index:
            scale, cash = factors[bisect_right(times, bar.time)]
            for field in ('open', 'high', 'low', 'close', 'volume'):
                value = values[field]
                if value is not None:
                    adjusted = value / scale if field == 'volume' else value * scale - cash
                    if not isfinite(adjusted):
                        raise ValueError('Adjusted candle exceeds numeric range.')
                    values[field] = adjusted
        result.append(values)
    return dict(schema_version='openpine.candle-prices.1', profile=profile, as_of_ms=cutoff,
                snapshot_id=source.manifest['snapshot_id'], actions_id=index.sha256 if index else None,
                start=start, next=start+limit if len(page)>limit else None, bars=result)
