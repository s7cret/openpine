"""UTC/24x7 candle boundaries shared by acquisition, execution and recovery."""

import calendar
import re
from datetime import UTC, datetime, timedelta

MAX_TIME=253402214400000
UTC_EPOCH=datetime(1970,1,1,tzinfo=UTC)


def utc_datetime(timestamp):
    if type(timestamp) is not int or not 0<=timestamp<MAX_TIME:raise ValueError('Timestamp outside supported UTC calendar.')
    return UTC_EPOCH+timedelta(milliseconds=timestamp)


def utc_milliseconds(date):return (date-UTC_EPOCH)//timedelta(milliseconds=1)



def timeframe(value):
    if type(value) is not str or not (value in ('D','W','M') or re.fullmatch(r'[1-9][0-9]{0,3}',value) and int(value)<=1440):
        raise ValueError('Use canonical minute intervals 1..1440 or D/W/M.')
    return value


def floor_open(timestamp,interval):
    timeframe(interval)
    if type(timestamp) is not int or not 0<=timestamp<MAX_TIME:raise ValueError('Timestamp outside supported UTC calendar.')
    if interval.isdigit():
        duration=int(interval)*60000
        return timestamp//duration*duration
    date=utc_datetime(timestamp).replace(hour=0,minute=0,second=0,microsecond=0)
    if interval=='W':date-=timedelta(days=date.weekday())
    if interval=='M':date=date.replace(day=1)
    return utc_milliseconds(date)


def next_open(timestamp,interval):
    start=floor_open(timestamp,interval)
    if interval.isdigit():return start+int(interval)*60000
    if interval in ('D','W'):return start+(86400000 if interval=='D' else 604800000)
    date=utc_datetime(start)
    return start+calendar.monthrange(date.year,date.month)[1]*86400000


def aligned(timestamp,interval):return type(timestamp) is int and timestamp>=0 and floor_open(timestamp,interval)==timestamp


def shift_open(timestamp,interval,count):
    if type(count) is not int:raise ValueError('Calendar shift requires an integer count.')
    start=floor_open(timestamp,interval)
    if interval=='M':
        date=utc_datetime(start);index=date.year*12+date.month-1+count
        year,month=divmod(index,12)
        if not 1970<=year<=9999:raise ValueError('Calendar shift leaves the supported range.')
        return utc_milliseconds(datetime(year,month+1,1,tzinfo=UTC))
    duration=int(interval)*60000 if interval.isdigit() else 86400000 if interval=='D' else 604800000
    result=start+count*duration
    if not 0<=result<MAX_TIME:raise ValueError('Calendar shift leaves the supported range.')
    return result


def requestable(primary,requested):
    timeframe(primary);timeframe(requested)
    if primary.isdigit() and requested.isdigit():return int(requested)>=int(primary) and int(requested)%int(primary)==0
    if primary.isdigit():return 1440%int(primary)==0
    if requested.isdigit():return False
    return {'D':1,'W':2,'M':3}[requested]>={'D':1,'W':2,'M':3}[primary]
