"""Pure UTC fields and explicit historical/observed bar-phase flags."""

from datetime import UTC, datetime, timedelta
from decimal import Decimal, localcontext
from math import isfinite
from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.calendar import MAX_TIME, UTC_EPOCH, next_open, pine_v6_period, utc_datetime, utc_milliseconds
from packages.contracts.calendar_zones import PARTS, WEEK
from packages.contracts.chart_clock import LAST_INDEX, LAST_TIME, TRADING_DAY
from packages.contracts.environment import INSTRUMENT_FIELDS
from packages.contracts.scalar_functions import ScalarError
from packages.runtime.series import KernelError
from packages.runtime.time_format import TimeFormatError, load_zone, wall_instants


def date_part(name, date):
    if name == WEEK:
        return date.isocalendar().week
    if name == 'dayofweek':
        return (date.weekday() + 1) % 7 + 1
    return getattr(date, 'day' if name == 'dayofmonth' else name)


def calendar_value(name, timestamp):
    if timestamp is None:return None
    if type(timestamp) is not int or not 0<=timestamp<MAX_TIME:raise KernelError('ENVIRONMENT_TIME','Timestamp leaves the UTC calendar.','rule.environment')
    return date_part(name, utc_datetime(timestamp))


def timestamp(arguments, context):
    if any(v is None for v in arguments):return None
    try:
        value=utc_milliseconds(datetime(*arguments,tzinfo=UTC))
        if not 0<=value<MAX_TIME:raise ValueError('out of range')
        return value
    except (ValueError,OverflowError,TypeError) as error:
        raise KernelError('ENVIRONMENT_DATE','Invalid UTC calendar date/time.','rule.environment') from error


def calendar_kernel(name):
    return lambda arguments,context:calendar_value(name,arguments[0])


def calendar_zone(name):
    if type(name) is not str or not 1 <= len(name) <= 128:
        raise KernelError('CALENDAR_ZONE', 'A time zone name of 1 to 128 characters is required.', 'rule.environment')
    try:
        return load_zone(name)
    except TimeFormatError as error:
        code = 'CALENDAR_ZONE' if error.code == 'TIME_FORMAT_ZONE' else error.code
        raise KernelError(code, str(error), 'rule.environment') from error


def zoned_calendar_kernel(name):
    def evaluate(arguments, context):
        stamp, *zones = arguments
        if stamp is None:
            return None
        zone = calendar_zone(zones[0]) if zones else UTC
        try:
            date = (UTC_EPOCH + timedelta(milliseconds=stamp)).astimezone(zone)
        except (OverflowError, ValueError) as error:
            raise KernelError('CALENDAR_RANGE', 'Timestamp leaves years 0001 through 9999 in UTC or the selected zone.',
                              'rule.environment') from error
        return date_part(name, date)

    return evaluate


def zoned_timestamp(arguments, context):
    name, *numbers = arguments
    if any(value is None for value in numbers):
        return None
    zone = calendar_zone(name)
    try:
        wall = datetime(*numbers)
    except (ValueError, OverflowError, TypeError) as error:
        raise KernelError('ENVIRONMENT_DATE', 'Invalid local calendar date or time.', 'rule.environment') from error
    try:
        matches = wall_instants(wall, zone)
    except (OverflowError, ValueError) as error:
        raise KernelError('CALENDAR_RANGE', 'Local date leaves years 0001 through 9999 in UTC.',
                          'rule.environment') from error
    if not matches:
        raise KernelError('TIMESTAMP_NONEXISTENT', 'Local time does not exist in the selected zone.', 'rule.environment')
    if len(matches) != 1:
        raise KernelError('TIMESTAMP_AMBIGUOUS', 'Local time occurs twice in the selected zone.', 'rule.environment')
    return utc_milliseconds(matches.pop())


def rounded_price(value, mintick):
    # Integer division keeps half-tick comparisons exact on either side of zero.
    price, tick = Decimal(str(value)), Decimal(mintick)
    price_num, price_den = price.as_integer_ratio()
    tick_num, tick_den = tick.as_integer_ratio()
    denominator = price_den * tick_num
    ticks, remainder = divmod(price_num * tick_den, denominator)
    if 2 * remainder >= denominator:
        ticks += 1
    coefficient = Decimal(ticks)
    with localcontext() as ctx:
        ctx.prec = len(coefficient.as_tuple().digits) + len(tick.as_tuple().digits)
        return coefficient * tick


def tick_kernel(*, string=False):
    def evaluate(arguments, context):
        value = arguments[0]
        if value is None:
            return 'NaN' if string else None
        if context.instrument is None:
            raise KernelError('ENVIRONMENT_METADATA', 'The selected snapshot has no frozen instrument metadata.',
                              'rule.environment', category='INPUT_UNAVAILABLE')
        if type(value) not in (int, float) or not isfinite(value):
            raise KernelError('NUMERIC_RANGE', 'Tick rounding requires a finite numeric value.', 'rule.environment')
        rounded = rounded_price(value, context.instrument.mintick)
        try:
            context.scalar_budget.charge(1)
            if string:
                places = max(0, -rounded.as_tuple().exponent)
                size = max(1, rounded.adjusted() + 1) + (places + 1 if places else 0) + int(rounded.is_signed())
                context.scalar_budget.output(size)
                return format(rounded, 'f')
        except ScalarError as error:
            raise KernelError('SCALAR_LIMIT', str(error), 'rule.environment') from error
        result = float(rounded)
        if not isfinite(result):
            raise KernelError('NUMERIC_OVERFLOW', 'Tick rounding leaves the finite real range.', 'rule.environment')
        return result

    return evaluate


def reference(evaluation, node):
    env=evaluation.environment;frame=evaluation.frame;slot=node.contract.slot.removeprefix('environment.')
    def fail(code,text):raise OpenPineError(Diagnostic('INPUT_UNAVAILABLE',code,text,node.span,'rule.environment',evaluation.program.identity.language_version))
    if env is None or frame is None:fail('ENVIRONMENT_REQUIRED','This program requires a pinned execution environment and current bar.')
    if slot=='time':return frame.time
    if slot=='time_close':return next_open(frame.time,env.timeframe)
    if slot in (LAST_TIME, LAST_INDEX):
        if env.mode == 'historical':
            if env.dataset_last_ms is None:
                fail('LAST_BAR_DATASET_END', 'This program needs the frozen last candle of the dataset.')
            return env.dataset_last_ms if slot == LAST_TIME else env.count - 1
        if evaluation.environment_phase == 'historical':
            if slot == LAST_TIME:
                return env.history_last_ms
            if env.history_last_index is None:
                fail('LAST_BAR_HISTORY_INDEX', 'A historical replay prefix requires the pinned last bar index.')
            return env.history_last_index
        return frame.time if slot == LAST_TIME else frame.bar_index
    if slot == TRADING_DAY:
        day = 86_400_000
        stamp = next_open(frame.time, env.timeframe) - 1 if env.timeframe in ('W', 'M') else frame.time
        return stamp // day * day
    if slot in PARTS:return calendar_value(slot,frame.time)
    if slot.startswith('barstate.'):
        flag=slot.removeprefix('barstate.');phase=evaluation.environment_phase
        historical=phase=='historical';confirmed=historical or phase in ('close','close_first')
        if flag=='isfirst':return frame.bar_index==0
        if flag=='islast':return not historical or (env.count is not None and frame.bar_index==env.count-1)
        if flag=='ishistory':return historical
        if flag=='isrealtime':return not historical
        if flag=='isnew':return historical or phase in ('open','close_first')
        if flag=='isconfirmed':return confirmed
        if flag=='islastconfirmedhistory':return historical and (frame.time==env.history_last_ms if env.mode=='observed' else frame.bar_index==env.count-1)
    if slot.startswith('timeframe.'):
        field=slot.removeprefix('timeframe.');tf=env.timeframe
        return {'period':tf,'period_v6':pine_v6_period(tf),'multiplier':int(tf) if tf.isdigit() else 1,
            'isintraday':tf.isdigit(),'isminutes':tf.isdigit(),'isseconds':False,
            'isdaily':tf=='D','isweekly':tf=='W','ismonthly':tf=='M','isdwm':tf in ('D','W','M')}[field]
    if slot.startswith('syminfo.'):
        field=slot.removeprefix('syminfo.')
        if field in INSTRUMENT_FIELDS:
            if env.instrument is None:fail('ENVIRONMENT_METADATA','The selected snapshot has no frozen instrument metadata.')
            value=env.instrument[field]
            return float(value) if field in ('mintick','pointvalue') else value
        if field=='session':return '24x7'
        if field=='timezone':return 'UTC'
        if ':' not in env.symbol:fail('ENVIRONMENT_SYMBOL','Instrument properties require a qualified EXCHANGE:TICKER symbol.')
        prefix,ticker=env.symbol.split(':',1)
        return {'tickerid':env.symbol,'ticker':ticker,'root':ticker,'prefix':prefix}[field]
    fail('ENVIRONMENT_FIELD','Unknown environment field.')
