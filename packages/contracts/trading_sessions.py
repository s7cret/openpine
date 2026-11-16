"""Bounded time-based Pine sessions and the physical inputs to their kernels."""

import re
from functools import lru_cache

from .model import LiteralIR, ReferenceIR, UnaryIR, ValueType
from .technical import validate_market_arguments


OPERATIONS = {'time': 'environment.session_open', 'time_close': 'environment.session_close'}
OFFSET_OPERATIONS = {name: operation + '_offset' for name, operation in OPERATIONS.items()}
OFFSET_PARAMETERS = ('bars_back', 'timeframe_bars_back')
PAST_LIMIT = 5000
FUTURE_LIMIT = 500
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


def offset_parameters(version):
    return public_parameters(version) + OFFSET_PARAMETERS[:version - 4]


def period_target(node):
    """The target to index when the second offset can read observed periods."""
    if node.operation not in OFFSET_OPERATIONS.values() or len(node.arguments) != 7:
        return None
    offset = node.arguments[-1].expression
    if (isinstance(offset, LiteralIR) and type(offset.value) is int and offset.value <= 0
            or isinstance(offset, UnaryIR) and offset.operator == '-'
            and isinstance(offset.operand, LiteralIR)
            and type(offset.operand.value) is int and offset.operand.value >= 0):
        return None
    return node.arguments[2].expression


def validate_period_target(program, node):
    target = period_target(node)
    if target is not None:
        from .computed_targets import evaluate
        from .model import Diagnostic, OpenPineError
        try:
            evaluate(program, target, allow_primary=True, shape_only=True)
        except OpenPineError as error:
            raise OpenPineError(Diagnostic(
                'PROFILE_UNCOVERED', 'TIME_OFFSET_TARGET',
                'A timeframe offset that can read history requires a frozen timeframe expression.',
                target.span, 'rule.environment', program.identity.language_version,
            )) from error


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
    if node.operation in OFFSET_OPERATIONS.values():
        for argument in zone[1:]:
            if argument.origin == 'default' and (
                    not isinstance(argument.expression, LiteralIR) or argument.expression.value != 0
                    or argument.expression.value_type != ValueType('int')):
                raise ValueError('The offset default must be the constant integer zero.')
