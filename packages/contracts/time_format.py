"""Versioned str.format_time defaults, verified against the pinned Pine reference."""

from .model import CallContract, LiteralIR, ReferenceIR, ValueType
from .wire import digest


NAME = 'str.format_time'
OPERATION = 'strings.format_time'
VERSIONS = (5, 6)
NAMES = ('time', 'format', 'timezone')
DEFAULT_PATTERN = "yyyy-MM-dd'T'HH:mm:ssZ"
DEFAULT_ZONE = 'syminfo.timezone'
TZDATA_VERSION = '2026.6'
IANA_VERSION = '2026f'
DEFAULTS = ({'kind': 'none'}, {'kind': 'literal', 'value': DEFAULT_PATTERN},
            {'kind': 'reference', 'name': DEFAULT_ZONE})


def contract(version):
    identifier = f'openpine.time-format.v{version}'
    signature = {'name': NAME, 'parameters': NAMES, 'defaults': DEFAULTS,
                 'rule': 'unix_milliseconds_to_series_string.1', 'iana': IANA_VERSION}
    return CallContract('symbol.' + identifier, 'overload.' + identifier, 'assertion.' + identifier,
                        digest(signature), OPERATION, tuple(f'parameter.{identifier}.{name}' for name in NAMES),
                        (('int',), ('string',), ('string',)), ('series',) * 3,
                        None, (), ValueType('string'), 'fixed_series')


def validate_defaults(call):
    time, pattern, timezone = call.arguments
    if time.origin != 'source':
        raise ValueError('The time argument must originate in the source.')
    value = pattern.expression
    if pattern.origin == 'default' and (not isinstance(value, LiteralIR)
            or value.value != DEFAULT_PATTERN or value.value_type != ValueType('string')):
        raise ValueError('The time format default differs from its fixed pattern.')
    value = timezone.expression
    if timezone.origin == 'default' and (not isinstance(value, ReferenceIR)
            or value.contract.slot != 'environment.' + DEFAULT_ZONE):
        raise ValueError('The time zone default must be the bound syminfo.timezone reference.')
