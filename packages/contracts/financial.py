"""Fiscal-period identities and retrospective/point-in-time report indices."""
from math import isfinite

UNITS = {
    'TOTAL_REVENUE': 'money',
    'GROSS_PROFIT': 'money',
    'EARNINGS_PER_SHARE_BASIC': 'money_per_share',
    'TOTAL_SHARES_OUTSTANDING': 'shares',
}
PARAMETERS = ('symbol', 'financial_id', 'period', 'gaps', 'ignore_invalid_symbol')


def supported(metric, period, version):
    if not isinstance(metric, str) or metric not in UNITS or version < 4:
        return False
    return (period in ('FQ', 'FY') or period == 'FH' and version >= 5
            or period == 'TTM' and UNITS[metric] != 'shares')


def stream_key(record):
    key = (record['symbol'], record['family'])
    if record['family'] == 'financial':
        return (*key, record['financial_id'], record['period'])
    return key


def normalize_events(stream):
    from .corporate import fail, timestamp
    metric = stream['financial_id']
    if not supported(metric, stream['period'], 6) or stream['unit'] != UNITS[metric]:
        fail('ACTION_SCHEMA', 'Invalid financial metric, period or unit.')
    rows = {}
    boundaries = {}
    for row in stream['events']:
        if type(row) is not dict or set(row) != {'period_start_ms', 'period_end_ms', 'next_start_ms', 'available_ms', 'value'}:
            fail('ACTION_SCHEMA', 'Invalid financial report fields.')
        start, end, following, available = (row[k] for k in ('period_start_ms', 'period_end_ms', 'next_start_ms', 'available_ms'))
        if (not all(map(timestamp, (start, end, following, available))) or not start <= end < following
                or available <= end or max(available, following) >= stream['end_ms']):
            fail('ACTION_SCHEMA', 'Invalid financial reporting dates.')
        if boundaries.setdefault(start, (end, following)) != (end, following):
            fail('ACTION_CONFLICT', 'Conflicting fiscal boundaries.')
        number = row['value']
        if number is not None and (type(number) not in (int, float) or abs(number) > 1e308 or not isfinite(number)):
            fail('ACTION_VALUE', 'Expected a finite report value or null.')
        normalized = {**row, 'value': None if number is None else float(number)}
        if rows.setdefault((start, available), normalized) != normalized:
            fail('ACTION_CONFLICT', 'Conflicting financial publication.')
    ordered = [boundaries[k] for k in sorted(boundaries)]
    if any(a[0] >= b[0] or a[1] >= b[1] for a, b in zip(ordered, ordered[1:])):
        fail('ACTION_CONFLICT', 'Inconsistent order of fiscal boundaries.')
    return [rows[key] for key in sorted(rows)]


def timelines(stream):
    latest = {}
    releases = {}
    for row in stream['events']:
        start = row['period_start_ms']
        latest[start] = row
        clock = max(row['available_ms'], row['next_start_ms'])
        previous = releases.get(clock)
        if previous is None or (start, row['available_ms']) > (previous['period_start_ms'], previous['available_ms']):
            releases[clock] = row
    historical = list(latest.values())
    times, reports = [], []
    for clock, row in sorted(releases.items()):
        if reports and row['period_start_ms'] < reports[-1]['period_start_ms']:
            continue
        times.append(clock)
        reports.append(row)
    return ((stream, [row['next_start_ms'] for row in historical], historical),
            (stream, times, reports))
