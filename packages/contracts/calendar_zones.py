"""Versioned calendar arguments and the numeric timestamp ABI."""

from .environment import CALENDAR
from .model import LiteralIR, ValueType


WEEK = 'weekofyear'
PARTS = (*CALENDAR, WEEK)
WEEK_SLOT = 'environment.' + WEEK
ZONED_PARTS = {name: 'environment.calendar_zone.' + name for name in PARTS}
TIMESTAMP = 'environment.timestamp_zone'
CALENDAR_PARAMETERS = ('time', 'timezone')
TIMESTAMP_PARAMETERS = ('timezone', 'year', 'month', 'day', 'hour', 'minute', 'second')


def optional_fields(version):
    return ('second',) if version == 3 else ('hour', 'minute', 'second')


def validate_arguments(call, version):
    optional = optional_fields(version) if call.operation == TIMESTAMP else ()
    names = TIMESTAMP_PARAMETERS if call.operation == TIMESTAMP else CALENDAR_PARAMETERS
    for name, argument in zip(names, call.arguments, strict=True):
        if argument.origin != 'default':
            continue
        value = argument.expression
        if (name not in optional or not isinstance(value, LiteralIR)
                or value.value_type != ValueType('int') or type(value.value) is not int or value.value != 0):
            raise ValueError('Only optional clock fields may default to the constant integer zero.')
