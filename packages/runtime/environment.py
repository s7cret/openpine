"""Pure UTC fields and explicit historical/observed bar-phase flags."""

from datetime import UTC, datetime
from decimal import Decimal, localcontext
from math import isfinite
from packages.contracts import Diagnostic, OpenPineError
from packages.contracts.calendar import MAX_TIME, next_open,utc_datetime,utc_milliseconds
from packages.contracts.environment import CALENDAR, INSTRUMENT_FIELDS
from packages.contracts.scalar_functions import ScalarError
from packages.runtime.series import KernelError


def calendar_value(name, timestamp):
    if timestamp is None:return None
    if type(timestamp) is not int or not 0<=timestamp<MAX_TIME:raise KernelError('ENVIRONMENT_TIME','Timestamp leaves the UTC calendar.','rule.environment')
    date=utc_datetime(timestamp)
    return {'year':date.year,'month':date.month,'dayofmonth':date.day,
        'dayofweek':(date.weekday()+1)%7+1,'hour':date.hour,'minute':date.minute,'second':date.second}[name]


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
    if slot in CALENDAR:return calendar_value(slot,frame.time)
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
        return {'period':tf,'multiplier':int(tf) if tf.isdigit() else 1,
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
