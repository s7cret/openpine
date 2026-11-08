"""Closed visual OHLC contracts and source-aligned samples."""

import math

from .colors import valid
from .visual_display import with_display
from .visual_panes import FLAG, WINDOW, overlay_api, placement, window_api, validate_window, matches_default
from .wire import digest, read_json

PROFILE = 'openpine.visual-ohlc.1'
SCHEMA = 'openpine.ohlc-layer.1'
KINDS = ('plotbar', 'plotcandle')
OPERATIONS = tuple(f'ohlc.{name}' for name in KINDS)
PRICES = ('open', 'high', 'low', 'close')
COLORS = ('color', 'wickcolor', 'bordercolor')
DEFAULT_COLOR = '#2196F3FF'


def policy(version):
    return {
        'profile_id': PROFILE,
        'language_version': version,
        'basis': 'legacy_reconstruction' if version < 3 else 'pinned_reference',
        'scope': 'direct_global_calls_only',
        'sample': 'any_missing_ohlc_omits_sample; high_and_low_are_extrema_of_all_four_prices',
        'color_default': DEFAULT_COLOR,
        'title_default': '',
        'force_overlay': version >= 5,
        'show_last': version >= 3,
        'external_execution_verified': False,
    }


def apis(version):
    result = []
    for name in KINDS:
        parameters = [dict(name=p, types=['int', 'float'], qualifier='series') for p in PRICES]
        parameters.append(dict(name='title', types=['string'], qualifier='const'))
        names = COLORS if name == 'plotcandle' and version >= 3 else COLORS[:1]
        parameters.extend(dict(name=p, types=['color'], qualifier='series') for p in names)
        api = dict(id=f'{PROFILE}.v{version}.{name}', name=name, operation=f'ohlc.{name}',
                   parameters=parameters, defaults=['', *([DEFAULT_COLOR] * len(names))],
                   result='void', result_rule='fixed', effects=['emits_plot'], named=version >= 3)
        result.append(overlay_api(api) if version >= 5 else api)
        if version >= 3:
            window = window_api(api, version)
            result.append(overlay_api(window) if version >= 5 else window)
    return with_display(result,version)


def contracts(version):
    from .model import CallContract, ValueType
    return {api['id']: CallContract(
        'symbol.' + api['id'], 'overload.' + api['id'], 'assertion.' + api['id'], digest(api), api['operation'],
        tuple('parameter.' + api['id'] + '.' + p['name'] for p in api['parameters']),
        tuple(tuple(p['types']) for p in api['parameters']), tuple(p['qualifier'] for p in api['parameters']),
        None, tuple(api['effects']), ValueType('void'), 'fixed') for api in apis(version)}


def admit(root, snapshot, version):
    profile = read_json(root / 'spec/visual-ohlc-profile.json')
    if (profile.get('schema_version') != 'openpine.visual-ohlc-spec.1'
            or profile['source_lock_sha256'] != digest(snapshot.lock)
            or profile['versions'][str(version)] != {
                'policy': policy(version), 'apis': apis(version),
                'reference_sha256': digest(snapshot.references[version])}):
        raise ValueError('Visual OHLC profile differs from pinned evidence.')


def validate_call(node, version):
    api = next((a for a in apis(version) if 'overload.' + a['id'] == node.overload_id), None)
    if api is None or node.operation != api['operation']:
        raise ValueError('Unknown visual OHLC ABI.')
    for i, arg in enumerate(node.arguments):
        if arg.origin == 'default' and (i < len(PRICES)
                or not matches_default(arg.expression, api['defaults'][i - len(PRICES)])):
            raise ValueError('Visual OHLC default differs from its closed signature.')


def validate_sample(sample, kind=None):
    if sample is None:
        return
    if type(sample) is not dict or sample.keys() != {*PRICES, *COLORS}:
        raise ValueError('Invalid visual OHLC sample fields.')
    prices = tuple(sample[k] for k in PRICES)
    if (any(type(v) is not float or not math.isfinite(v) for v in prices)
            or prices[1] != max(prices) or prices[2] != min(prices)
            or any(not valid(sample[k]) for k in COLORS)
            or kind == 'plotbar' and any(sample[k] is not None for k in COLORS[1:])):
        raise ValueError('Invalid visual OHLC values or colors.')


def validate_event(value, operation, version):
    if (type(value) is not dict or value.keys() - {FLAG, WINDOW} != {'kind', 'bar'}
            or operation not in OPERATIONS or value['kind'] != operation.removeprefix('ohlc.')):
        raise ValueError('Invalid visual OHLC event.')
    placement(value, version)
    validate_window(value, version)
    validate_sample(value['bar'], value['kind'])


def metadata(value):
    if (type(value) is not dict or value.keys() - {FLAG, WINDOW} != {'schema_version', 'kind'}
            or value['schema_version'] != SCHEMA or value['kind'] not in KINDS):
        raise ValueError('Invalid visual OHLC descriptor.')
    validate_window(value, 6)
    return {'presentation_schema': SCHEMA, **{k:v for k,v in value.items() if k!='schema_version'},
            **placement(value, 6)}


def validate_row(row, descriptor=None):
    if (type(row) is not dict or row.keys() != {'value', 'bar'} or row['value'] is not None
            or descriptor is not None and descriptor.get('schema_version') != SCHEMA):
        raise ValueError('Invalid visual OHLC row.')
    validate_sample(row['bar'], descriptor['kind'] if descriptor else None)
