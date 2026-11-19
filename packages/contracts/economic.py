"""Country-level economic releases and their publication-time index."""
from math import isfinite

PARAMETERS = ('country_code', 'field', 'gaps', 'ignore_invalid_symbol')
UNITS = {('US','GDP'): 'billion_USD', ('US','GDPQQ'): 'percent'}


def key(record):
    return ('economic', record['country_code'], record['field'])


def normalize(stream):
    from .corporate import fail, timestamp
    if set(stream) != {'family','country_code','field','unit','start_ms','end_ms','events'}:
        fail('ECONOMIC_SCHEMA', 'Invalid economic stream fields.')
    country, field = stream['country_code'], stream['field']
    if (type(country) is not str or type(field) is not str or (country,field) not in UNITS
            or stream['unit'] != UNITS[country,field]):
        fail('ECONOMIC_SCHEMA', 'Unsupported country, economic field or unit.')
    start, end = stream['start_ms'], stream['end_ms']
    if not timestamp(start) or not timestamp(end) or start >= end or type(stream['events']) is not list:
        fail('ECONOMIC_SCHEMA', 'Invalid economic stream coverage.')
    events = {}
    for row in stream['events']:
        if type(row) is not dict or set(row) != {'period_end_ms','available_ms','value'}:
            fail('ECONOMIC_SCHEMA', 'Invalid economic release fields.')
        at, period, value = row['available_ms'], row['period_end_ms'], row['value']
        if not timestamp(at) or not timestamp(period) or not period < at < end:
            fail('ECONOMIC_SCHEMA', 'Expected publication after the reporting period and before the coverage end.')
        if value is not None and (type(value) not in (int,float) or abs(value)>1e308 or not isfinite(value)):
            fail('ECONOMIC_VALUE', 'Expected a finite economic observation or null.')
        item = {**row,'value':None if value is None else float(value)}
        if events.setdefault((at,period),item) != item:
            fail('ECONOMIC_CONFLICT', 'Conflicting revisions for one publication and period.')
    return {**stream,'events':[events[k] for k in sorted(events)]}


def timeline(stream):
    times, rows = [], []
    for row in stream['events']:
        if rows and row['period_end_ms'] < rows[-1]['period_end_ms']:
            continue
        times.append(row['available_ms'])
        rows.append(row)
    return stream,times,rows
