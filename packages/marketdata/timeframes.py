"""Explicit UTC interval capabilities, including variable calendar boundaries."""

import calendar
from datetime import UTC, datetime, timedelta

INTERVALS = {'1':'1m', '3':'3m', '5':'5m', '15':'15m', '30':'30m', '60':'1h',
             '120':'2h', '240':'4h', '360':'6h', '480':'8h', '720':'12h', 'D':'1d', 'W':'1w', 'M':'1M'}
EXECUTION = tuple(x for x in INTERVALS if x.isdigit())


def check(value):
    if type(value) is not str or value not in INTERVALS: raise ValueError('Unsupported canonical market timeframe.')
    return value


def floor_open(timestamp, timeframe):
    check(timeframe)
    if type(timestamp) is not int or not 0 <= timestamp < 253402214400000: raise ValueError('Timestamp outside supported UTC calendar.')
    if timeframe.isdigit():
        duration = int(timeframe) * 60000
        return timestamp // duration * duration
    date = datetime.fromtimestamp(timestamp / 1000, UTC).replace(hour=0, minute=0, second=0, microsecond=0)
    if timeframe == 'W': date -= timedelta(days=date.weekday())
    if timeframe == 'M': date = date.replace(day=1)
    return int(date.timestamp() * 1000)


def next_open(timestamp, timeframe):
    start = floor_open(timestamp, timeframe)
    if timeframe.isdigit(): return start + int(timeframe) * 60000
    if timeframe in ('D', 'W'): return start + (86400000 if timeframe == 'D' else 604800000)
    date = datetime.fromtimestamp(start / 1000, UTC)
    return start + calendar.monthrange(date.year, date.month)[1] * 86400000


def first_open(timestamp, timeframe):
    start = floor_open(timestamp, timeframe)
    return start if start == timestamp else next_open(timestamp, timeframe)


def capabilities():
    return {'schema_version':'openpine.provider-capabilities.1', 'provider':'binance.spot.2',
        'exchange':'binance', 'markets':['spot'], 'price_kinds':['trade'],
        'history_timeframes':list(INTERVALS), 'execution_timeframes':list(EXECUTION),
        'live_timeframes':list(EXECUTION), 'timezone':'UTC', 'session':'24x7',
        'calendar_execution':False, 'range':'[start,end)', 'max_history_bars':10000000,
        'live_contexts':1, 'live_gap_policy':'record_gap_reconcile_closed_bars_no_intrabar_reconstruction'}
