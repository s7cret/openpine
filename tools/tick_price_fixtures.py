"""Exact decimal oracles, recorded requests and a tick-aligned limit strategy."""

import argparse

from packages.contracts.contexts import MarketContext
from packages.contracts.wire import canonical
from tools.numeric_aggregates_fixtures import two_context_tape
from tools.specification import ROOT


DIRECTORY = 'conformance/tick-price'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/tick-price/two-tick-grids.pine'
STRATEGY = 'examples/tick-price/limit-price.pine'
TITLE = 'Tick price'
PRIMARY = MarketContext('tick-primary', 'TEST:TICK-CENT', '1', 'recorded.1')
HIGHER = MarketContext('tick-higher', 'TEST:TICK-QUARTER', '2', 'recorded.1')
REPLAY = (100.25,) * 4 + (101.,) * 4
HISTORICAL = (None,) + (100.25,) * 4 + (101.,) * 3
ALERT = (22, 'Tick price: 101.00')


def instrument(step):
    return {'mintick': step, 'pointvalue': '1', 'currency': 'USD', 'basecurrency': 'TEST', 'type': 'custom'}


ENVIRONMENTS = {
    PRIMARY.context_id: {'instrument': instrument('0.01'), 'history_last_ms': None},
    HIGHER.context_id: {'instrument': instrument('0.25'), 'history_last_ms': None},
}


def bars(context, candles):
    duration = int(context.timeframe) * 60_000
    return {'schema_version': 'openpine.historical-bars.2', 'context_id': context.context_id,
            'symbol': context.symbol, 'timeframe': context.timeframe,
            'bars': [dict(time=index * duration, open=opening, high=high, low=low, close=close, volume=1.)
                     for index, (opening, high, low, close) in enumerate(candles)]}


def market_input(data, step):
    return {'schema_version': 'openpine.market-input.1', 'data': data,
            'environment': {'schema_version': 'openpine.instrument-environment.1', 'instrument': instrument(step)}}


def strategy_input():
    context = MarketContext('tick-order', HIGHER.symbol, '1', 'uploaded-file.1')
    return market_input(bars(context, ((100.5, 101., 100., 100.5),
                                       (100.25, 101., 100., 100.5),
                                       (100.5, 101., 100., 100.5))), '0.25')


def tape():
    primary = ((100.125,) * 4,) * 8
    higher = ((100.125,) * 4, (100.125, 101.125, 100.125, 100.125),
              (100.125, 100.875, 100.125, 100.875), (100.125, 100.875, 100.125, 100.875))
    return two_context_tape(PRIMARY, HIGHER, primary, higher, preview_higher_index=1)


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def fixtures():
    files, cases = {}, []
    candles = tuple((value,) * 4 for value in (100.125, 100.375, 100.625, 100.875))
    data = bars(HIGHER, candles)
    files['bars.json'] = canonical(data) + b'\n'
    for name, tick in (('quarter', '0.25'), ('cent', '0.01'), ('fine', '0.00005')):
        files[name + '.json'] = canonical(market_input(data, tick)) + b'\n'

    def add(version, name, body, expected, market=None):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        case = dict(case_id=identifier, language_version=version, source=path, expected=expected)
        case.update({'market_input': market + '.json'} if market else {'bars': 'bars.json'})
        cases.append(case)

    def output(base, qualifier, values):
        if not isinstance(values, tuple):
            values = (values,) * len(candles)
        return qualifier, dict(title='', type=base, values=values)

    def executed(*outputs):
        qualifiers, rows = zip(*outputs, strict=True)
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(candles), qualifiers=qualifiers, outputs=rows)

    def rejected(category, code):
        return dict(status='rejected', category=category, code=code)

    for version in (1, 2, 3):
        add(version, 'round-unavailable', 'plot(round_to_mintick(close))',
            rejected('LANGUAGE_INVALID', 'SIGNATURE_UNAVAILABLE'))
        add(version, 'format-unavailable', 'plot(tostring(close,format.mintick)=="100.25"?1:0)',
            rejected('LANGUAGE_INVALID', 'UNDEFINED_VARIABLE'))
    for version in (4, 5, 6):
        round_name = 'round_to_mintick' if version == 4 else 'math.round_to_mintick'
        string_name = 'tostring' if version == 4 else 'str.tostring'
        price_name, fmt_name = ('x', 'y') if version == 4 else ('value', 'format')
        add(version, 'quarter-grid',
            f'plot({round_name}(close))\nplot({round_name}(100.125))\nplot({round_name}(-100.125))\n'
            f'fmt=format.mintick\nplot({string_name}({fmt_name}=fmt,{price_name}=-100.125)=="-100.00"?1:0)',
            executed(output('float', 'series', (100.25, 100.5, 100.75, 101.)),
                     output('float', 'simple', 100.25), output('float', 'simple', -100.),
                     output('int', 'simple', 1)), 'quarter')
        add(version, 'decimal-grid',
            f'plot({round_name}(100.125))\nplot({round_name}(-0.005))\n'
            f'plot({string_name}(-0.005,format.mintick)=="0.00"?1:0)\n'
            f'plot({string_name}(-1.5,"#")=="-2"?1:0)',
            executed(output('float', 'simple', 100.13), output('float', 'simple', 0.),
                     output('int', 'simple', 1), output('int', 'simple', 1)), 'cent')
        add(version, 'small-grid',
            f'plot({round_name}(1.000025))\nplot({string_name}(1.000024,format.mintick)=="1.00000"?1:0)',
            executed(output('float', 'simple', 1.00005), output('int', 'simple', 1)), 'fine')
        add(version, 'missing-values',
            f'plot({round_name}(na))\nplot({string_name}(na,format.mintick)=="NaN"?1:0)',
            executed(output('float', 'simple', None), output('int', 'simple', 1)))
        add(version, 'missing-round-step', f'plot({round_name}(close))',
            rejected('INPUT_UNAVAILABLE', 'ENVIRONMENT_METADATA'))
        add(version, 'missing-format-step', f'plot({string_name}(close,format.mintick)=="100.25"?1:0)',
            rejected('INPUT_UNAVAILABLE', 'ENVIRONMENT_METADATA'))
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-calculated decimal tick grids and exact half-ticks, plus version and metadata checks.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / DIRECTORY
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, data in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == data, name
        else:
            path.write_bytes(data)
    print('Tick-price fixtures verified.')


if __name__ == '__main__':
    main()
