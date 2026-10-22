"""Closed research contracts; no scheduler, storage, or provider dependencies."""
from decimal import Decimal, InvalidOperation

SEARCH_SCHEMA = 'openpine.search.1'
METRICS_SCHEMA = 'openpine.search-metrics.1'
ALGORITHM = 'openpine.finite-search.sha256.1'
MAX_CANDIDATES = 256
STRATEGY_METRICS = ('net_profit', 'max_drawdown', 'trades', 'commission')
OUTPUT_METRICS = ('output_last', 'output_mean', 'output_min', 'output_max')


def decimal_value(value):
    # A 700-significant-digit mean at exponent -300 needs up to 1003
    # characters in fixed notation, including its sign and leading zeroes.
    if type(value) not in (str, int, float, Decimal) or len(str(value)) > 1024:
        raise ValueError('Use a finite decimal with at most 1024 characters.')
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError('Invalid decimal.') from error
    if not result.is_finite() or result and not -300 <= result.adjusted() <= 300:
        raise ValueError('Decimal is outside the finite research range.')
    return result


def decimal_text(value):
    value = decimal_value(value)
    return format(value, 'f') if value else '0'


def evaluation_window(value):
    names = {'warmup_start_ms', 'score_start_ms', 'end_ms'}
    if (type(value) is not dict or value.keys() != names
            or any(type(value[k]) is not int for k in names)
            or not 0 <= value['warmup_start_ms'] <= value['score_start_ms'] < value['end_ms'] <= 2**63-1):
        raise ValueError('Window requires warmup_start_ms <= score_start_ms < end_ms, in UTC milliseconds.')
    return dict(value)


def split_windows(value):
    if type(value) is not dict or value.keys() != {'selection', 'holdout'}:
        raise ValueError('Provide selection and holdout windows.')
    result = {k: evaluation_window(value[k]) for k in ('selection', 'holdout')}
    if result['selection']['end_ms'] > result['holdout']['score_start_ms']:
        raise ValueError('Selection must finish before the holdout scoring interval.')
    return result


def metric_spec(value, *, objective=False):
    required = {'metric', 'direction'} if objective else {'metric', 'operator', 'value'}
    if type(value) is not dict or not required <= value.keys() <= required | {'output_index'}:
        raise ValueError('Invalid objective/constraint fields.')
    metric = value['metric']
    if metric not in STRATEGY_METRICS + OUTPUT_METRICS:
        raise ValueError('Unknown research metric.')
    if metric in OUTPUT_METRICS:
        if type(value.get('output_index')) is not int or not 0 <= value['output_index'] < 32:
            raise ValueError('Output metrics require an explicit output_index in 0..31.')
    elif 'output_index' in value:
        raise ValueError('Broker metrics do not take output_index.')
    result = dict(value)
    if objective:
        if value['direction'] not in ('maximize', 'minimize'):
            raise ValueError('Objective direction must be maximize or minimize.')
    else:
        if value['operator'] not in ('gte', 'lte'):
            raise ValueError('Constraint operator must be gte or lte.')
        result['value'] = decimal_text(value['value'])
    return result


def metric_key(spec):
    return spec['metric'] + (':' + str(spec['output_index']) if spec['metric'] in OUTPUT_METRICS else '')
