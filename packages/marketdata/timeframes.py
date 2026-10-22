"""Explicit UTC interval capabilities, including variable calendar boundaries."""

from packages.contracts.calendar import floor_open as calendar_floor,next_open as calendar_next

INTERVALS = {'1':'1m', '3':'3m', '5':'5m', '15':'15m', '30':'30m', '60':'1h',
             '120':'2h', '240':'4h', '360':'6h', '480':'8h', '720':'12h', 'D':'1d', 'W':'1w', 'M':'1M'}
EXECUTION = tuple(INTERVALS)


def check(value):
    if type(value) is not str or value not in INTERVALS: raise ValueError('Unsupported canonical market timeframe.')
    return value


def floor_open(timestamp, timeframe):
    check(timeframe)
    return calendar_floor(timestamp,timeframe)


def next_open(timestamp, timeframe):
    check(timeframe)
    return calendar_next(timestamp,timeframe)


def first_open(timestamp, timeframe):
    start = floor_open(timestamp, timeframe)
    return start if start == timestamp else next_open(timestamp, timeframe)


def capabilities():
    return {'schema_version':'openpine.provider-capabilities.1', 'provider':'binance.spot.2',
        'exchange':'binance', 'markets':['spot'], 'price_kinds':['trade'],
        'history_timeframes':list(INTERVALS), 'execution_timeframes':list(EXECUTION),
        'live_timeframes':list(EXECUTION), 'timezone':'UTC', 'session':'24x7',
        'calendar_execution':True, 'range':'[start,end)', 'max_history_bars':10000000,
        'live_contexts':8, 'max_physical_feeds':8, 'live_gap_policy':'record_gap_reconcile_closed_bars_no_intrabar_reconstruction'}
