"""Translate the current physical bar to a time-based session window."""

from datetime import UTC, datetime, time, timedelta
from functools import lru_cache

from packages.contracts.calendar import canonical_timeframe, floor_open, next_open, requestable, utc_datetime, utc_milliseconds
from packages.contracts.trading_sessions import CALENDAR_ABI, DAY, parse_session
from packages.runtime.series import KernelError
from packages.runtime.time_format import TimeFormatError, load_zone, wall_instants


def error(code, message):
    return KernelError(code, message, 'rule.environment')


@lru_cache(maxsize=512)
def local_boundary(day, minute, zone):
    wall = datetime.combine(day, time()) + timedelta(minutes=minute)
    matches = wall_instants(wall, zone)
    if len(matches) != 1:
        raise error('SESSION_DST_BOUNDARY', 'Session boundary must exist and be unambiguous in the selected zone.')
    return utc_milliseconds(matches.pop())


def evaluate(arguments, context, *, closing):
    stamp, chart_period, requested, session, *timezone = arguments
    calendar_call = context.overload_id.endswith(CALENDAR_ABI) and session in ('', '24x7')
    if calendar_call:
        try:
            chart_period = canonical_timeframe(chart_period)
            period = canonical_timeframe(requested) if requested else chart_period
            if not requestable(chart_period, period):
                raise ValueError('Target is not an aligned equal or higher timeframe.')
        except ValueError as cause:
            raise error('SESSION_TIMEFRAME', 'Use an equal or compatible higher timeframe in minutes or D/W/M.') from cause
    elif not chart_period.isdigit() or requested not in ('', chart_period):
        raise error('SESSION_TIMEFRAME', 'Session calls require the current minute chart timeframe.')
    name = timezone[0] if timezone else 'UTC'
    if type(name) is not str or not 1 <= len(name) <= 128:
        raise error('SESSION_ZONE', 'A time zone of 1 to 128 characters is required.')
    try:
        zone = load_zone(name)
    except TimeFormatError as cause:
        raise error('SESSION_ZONE', str(cause)) from cause
    if calendar_call:
        if (period != chart_period or not chart_period.isdigit()) and zone != UTC:
            raise error('SESSION_ZONE', 'Calendar time calls use the UTC market calendar.')
        return next_open(stamp, period) if closing else floor_open(stamp, period)
    duration = int(chart_period) * 60_000
    if session in ('', '24x7'):
        return stamp + duration if closing else stamp
    try:
        windows, days = parse_session(session, weekdays_only=context.language_version < 5)
    except ValueError as cause:
        raise error('SESSION_STRING', str(cause)) from cause
    local = utc_datetime(stamp).astimezone(zone)
    day, minute = local.date(), local.hour * 60 + local.minute
    pine_day = (local.weekday() + 1) % 7 + 1
    for start, end in windows:
        overnight = start > 0 and end >= DAY
        after_midnight = overnight and minute < end - DAY
        elapsed = minute + DAY if after_midnight else minute
        trading_day = pine_day % 7 + 1 if overnight and not after_midnight else pine_day
        if trading_day not in days or not start <= elapsed < end:
            continue
        anchor = day - timedelta(days=after_midnight)
        first = local_boundary(anchor, start, zone)
        last = local_boundary(anchor, end, zone)
        if (stamp - first) % duration:
            raise error('SESSION_ALIGNMENT', 'Session opening must align with the current chart bar.')
        return min(stamp + duration, last) if closing else stamp
    return None
