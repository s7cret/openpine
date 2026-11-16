"""Deterministic timestamp presentation using the pinned IANA database."""

import re
from datetime import UTC, timedelta
from functools import cache, lru_cache
from importlib.resources import files
from zoneinfo import ZoneInfo

from packages.contracts.calendar import UTC_EPOCH, fixed_timezone
from packages.contracts.scalar_functions import MAX_STRING, ScalarError
from packages.contracts.time_format import IANA_VERSION, TZDATA_VERSION
from packages.runtime.series import KernelError


TOKENS = frozenset(('yyyy', 'MM', 'dd', 'HH', 'mm', 'ss', 'SSS', 'Z'))
PATTERN_PART = re.compile(r"''|'|([A-Za-z])\1*|[^'A-Za-z]+")
IANA_NAME = re.compile(r'[A-Za-z0-9_+-]+(?:/[A-Za-z0-9_+-]+)*')


class TimeFormatError(ValueError):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


@lru_cache(maxsize=128)
def parse_pattern(pattern):
    parts = []
    quoted = False
    for match in PATTERN_PART.finditer(pattern):
        part = match[0]
        if part == "''":
            parts.append(("'", True))
        elif part == "'":
            quoted = not quoted
        elif quoted or match[1] is None:
            parts.append((part, True))
        elif part in TOKENS:
            parts.append((part, False))
        else:
            raise TimeFormatError('TIME_FORMAT_PATTERN', f'Unsupported time format token {part[:32]!r}.')
    if quoted:
        raise TimeFormatError('TIME_FORMAT_PATTERN', 'Unclosed apostrophe quote in time format.')
    return tuple(parts)


@cache
def zone_names():
    try:
        from tzdata import IANA_VERSION as iana, __version__ as version
        names = files('tzdata').joinpath('zones').read_text(encoding='ascii').splitlines()
    except (ImportError, OSError) as error:
        raise TimeFormatError('TIME_FORMAT_TZDATA', 'Pinned IANA time zone data is unavailable.') from error
    if (version, iana) != (TZDATA_VERSION, IANA_VERSION):
        raise TimeFormatError('TIME_FORMAT_TZDATA', f'Time zone data must be tzdata {TZDATA_VERSION} ({IANA_VERSION}).')
    return frozenset(names)


@lru_cache(maxsize=128)
def load_zone(name):
    try:
        fixed = fixed_timezone(name)
    except ValueError as error:
        raise TimeFormatError('TIME_FORMAT_ZONE', str(error)) from error
    if fixed is not None:
        return fixed
    if not IANA_NAME.fullmatch(name):
        raise TimeFormatError('TIME_FORMAT_ZONE', 'Invalid IANA time zone name or UTC offset.')
    if name not in zone_names():
        raise TimeFormatError('TIME_FORMAT_ZONE', f'Unknown IANA time zone {name!r}.')
    try:
        resource = files('tzdata.zoneinfo').joinpath(*name.split('/'))
        with resource.open('rb') as stream:
            return ZoneInfo.from_file(stream, key=name)
    except (OSError, ValueError) as error:
        raise TimeFormatError('TIME_FORMAT_TZDATA', f'Cannot load pinned IANA time zone {name!r}.') from error


def wall_instants(local, zone):
    """Resolve a naive local date through both sides of a possible clock change."""
    candidates = set()
    for fold in (0, 1):
        instant = local.replace(tzinfo=zone, fold=fold).astimezone(UTC)
        if instant.astimezone(zone).replace(tzinfo=None) == local:
            candidates.add(instant)
    return candidates


def render(timestamp, pattern, name, budget):
    if type(pattern) is not str:
        raise TimeFormatError('TIME_FORMAT_PATTERN', 'A defined time format string is required.')
    if type(name) is not str or not 1 <= len(name) <= 128:
        raise TimeFormatError('TIME_FORMAT_ZONE', 'A time zone name of 1 to 128 characters is required.')
    if len(pattern) > MAX_STRING:
        raise ScalarError('Time format exceeds 4096 code points.')
    budget.charge(1 + len(pattern) + len(name))
    parts = parse_pattern(pattern)
    zone = load_zone(name)
    try:
        absolute = UTC_EPOCH + timedelta(milliseconds=0 if timestamp is None else timestamp)
        local = absolute.astimezone(zone)
    except (OverflowError, ValueError) as error:
        raise TimeFormatError('TIME_FORMAT_RANGE', 'Timestamp is outside years 0001 through 9999 in the selected zone.') from error

    seconds = local.utcoffset() // timedelta(seconds=1)
    hours, minutes = divmod(abs(seconds) // 60, 60)
    values = {
        'yyyy': f'{local.year:04d}', 'MM': f'{local.month:02d}', 'dd': f'{local.day:02d}',
        'HH': f'{local.hour:02d}', 'mm': f'{local.minute:02d}', 'ss': f'{local.second:02d}',
        'SSS': f'{local.microsecond // 1000:03d}', 'Z': f"{'-' if seconds < 0 else '+'}{hours:02d}{minutes:02d}",
    }
    pieces = [part if literal else values[part] for part, literal in parts]
    budget.output(sum(map(len, pieces)))
    return ''.join(pieces)


def kernel(arguments, context):
    try:
        return render(*arguments, context.scalar_budget)
    except ScalarError as error:
        raise KernelError('SCALAR_LIMIT', str(error), 'rule.scalar') from error
    except TimeFormatError as error:
        raise KernelError(error.code, str(error), 'rule.environment') from error
