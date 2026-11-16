"""Bounded time-based Pine sessions and the physical inputs to their kernels."""

import re
from functools import lru_cache

from .model import LiteralIR, ReferenceIR, ValueType
from .technical import validate_market_arguments


OPERATIONS = {'time': 'environment.session_open', 'time_close': 'environment.session_close'}
INPUT_VERSIONS = (3, 4, 5, 6)
DEFAULT_ZONE = 'syminfo.timezone'
MARKET_PARAMETERS = ('market_time', 'market_period')
CALENDAR_ABI = '.calendar'
MAX_WINDOWS = 8
DAY = 1440
WEEK = 7 * DAY
WINDOW = r'(?:[01][0-9]|2[0-3])[0-5][0-9]'
SESSION = re.compile(rf'{WINDOW}-{WINDOW}(?:,{WINDOW}-{WINDOW}){{0,{MAX_WINDOWS - 1}}}(?::[1-7]{{1,7}})?')


def input_profile(version):
    return f'openpine.session-input.v{version}.1'


def public_parameters(version):
    if version < 3:
        return ('x', 'y')
    return ('resolution' if version < 5 else 'timeframe', 'session') + (
        ('timezone',) if version >= 4 else ())


def admitted_input(snapshot, version):
    if version not in INPUT_VERSIONS:
        return False
    name = 'session' if version == 3 else 'input.session'
    category = 'constants' if version < 5 else 'functions'
    result = 'const string' if version < 5 else 'input string'
    return any(row['name'] == name and row['category'] == category and result in row['result_type_spellings']
               for row in snapshot.references[version]['entries'])


def minutes(value):
    return 60 * int(value[:2]) + int(value[2:])


@lru_cache(maxsize=128)
def parse_session(value, *, weekdays_only=False):
    """Return wall-clock windows with their Pine trading-day labels."""
    if value == '24x7':
        return ((0, DAY),), frozenset(range(1, 8))
    if type(value) is not str or len(value) > 87 or not SESSION.fullmatch(value):
        raise ValueError('Use HHmm-HHmm, up to eight windows, and optional days :1234567, or 24x7.')
    raw_windows, _, raw_days = value.partition(':')
    days = frozenset(map(int, raw_days or ('23456' if weekdays_only else '1234567')))
    windows = []
    weekly = []
    for raw in raw_windows.split(','):
        start, end = minutes(raw[:4]), minutes(raw[5:])
        overnight = start >= end and start != 0
        if start >= end:
            end += DAY
        windows.append((start, end))
        for day in days:
            begin = ((day - 1 - overnight) * DAY + start) % WEEK
            finish = begin + end - start
            weekly.append((begin, min(WEEK, finish)))
            if finish > WEEK:
                weekly.append((0, finish - WEEK))
    weekly.sort()
    if any(end > start for (_, end), (start, _) in zip(weekly, weekly[1:])):
        raise ValueError('Session windows must not overlap.')
    return tuple(windows), days


def validate_call(node, version):
    validate_market_arguments(node.arguments, ('time', 'timeframe.period'))
    timeframe, session, *zone = node.arguments[2:]
    if timeframe.origin != 'source':
        raise ValueError('The requested timeframe must originate in the source.')
    if session.origin == 'default' and (
            not isinstance(session.expression, LiteralIR) or session.expression.value != ''
            or session.expression.value_type != ValueType('string')):
        raise ValueError('The session default must select the current symbol session.')
    if version >= 4 and zone[0].origin == 'default' and (
            not isinstance(zone[0].expression, ReferenceIR)
            or zone[0].expression.contract.slot != 'environment.' + DEFAULT_ZONE):
        raise ValueError('The time zone default must be the bound syminfo.timezone reference.')
