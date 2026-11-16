"""Bounded absolute date literals and their portable input range."""

import re
from datetime import datetime

from .calendar import fixed_timezone, utc_milliseconds


VERSIONS = (4, 5, 6)
MAX_DATE_TEXT = 128
MIN_TIMESTAMP = -62135596800000
MAX_TIMESTAMP = 253402300799999
ISO_DATE = r'(?P<year>[0-9]{4})-(?P<month>[0-9]{2})-(?P<day>[0-9]{2})'
ENGLISH_DATE = r'(?P<day>[0-9]{1,2}) +(?P<month>[A-Za-z]{3}) +(?P<year>[0-9]{4})'
CLOCK = r'(?P<hour>[0-9]{2}):(?P<minute>[0-9]{2})(?::(?P<second>[0-9]{2})(?:\.(?P<fraction>[0-9]{1,3}))?)?'
ZONE = r'(?P<zone>Z|(?:UTC|GMT)(?:0|[+-][0-9]{1,2}(?::?[0-9]{2})?)?|[+-][0-9]{2}:?[0-9]{2})'
ISO = re.compile(ISO_DATE + r'(?:(?:T| +)' + CLOCK + r')?(?: *' + ZONE + r')?')
ENGLISH = re.compile(ENGLISH_DATE + r'(?: +' + CLOCK + r')?(?: +' + ZONE + r')?')
MONTHS = ('jan', 'feb', 'mar', 'apr', 'may', 'jun', 'jul', 'aug', 'sep', 'oct', 'nov', 'dec')


def input_profile(version):
    return f'openpine.date-input.v{version}.1'


def valid_timestamp(value):
    return type(value) is int and MIN_TIMESTAMP <= value <= MAX_TIMESTAMP


def parse_date(text):
    if type(text) is not str or len(text) > MAX_DATE_TEXT:
        raise ValueError('A date string can contain at most 128 characters.')
    text = text.strip(' ')
    iso = ISO.fullmatch(text)
    match = iso or ENGLISH.fullmatch(text)
    if match is None:
        raise ValueError('Use YYYY-MM-DD or DD Mon YYYY with optional time and UTC offset.')
    zone = match['zone'] or 'UTC'
    if zone == 'Z':
        zone = 'UTC'
    elif zone.startswith(('+', '-')):
        zone = 'UTC' + zone
    fixed = fixed_timezone(zone)
    try:
        month = int(match['month']) if iso else MONTHS.index(match['month'].lower()) + 1
        calendar_date = datetime(int(match['year']), month, int(match['day']), int(match['hour'] or 0),
                                 int(match['minute'] or 0), int(match['second'] or 0),
                                 int((match['fraction'] or '').ljust(3, '0')) * 1000, tzinfo=fixed)
    except ValueError as error:
        raise ValueError('Invalid calendar date or clock time.') from error
    value = utc_milliseconds(calendar_date)
    if not valid_timestamp(value):
        raise ValueError('The resulting UTC date is outside years 0001 through 9999.')
    return value
