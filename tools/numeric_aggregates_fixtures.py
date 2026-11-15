"""Independent numeric aggregate oracles and a confirmed-bar replay tape."""

import argparse
from dataclasses import replace

from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/numeric-aggregates'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/numeric-aggregates/confirmed-midpoint.pine'
TITLE = 'Numeric aggregates'
CLOSES = (6., 8., 10., 12.)
PRIMARY = MarketContext('midpoint-primary', 'TEST:MIDPOINT', '1', 'recorded.1')
HIGHER = MarketContext('midpoint-higher', PRIMARY.symbol, '2', PRIMARY.source)
HIGHER_OHLC = ((8., 10., 6., 8.), (8., 24., 4., 8.), (10., 20., 10., 12.), (5., 7., 3., 5.))
HISTORICAL = (None, 8., 8., 11., 11., 12., 12., 7.)
REPLAY = (8., 8., 8., 11., 12., 12., 7., 7.)
ALERT = (22, 'Midpoint breakout: 12')


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def tape():
    rows = []
    primary_bars = ((10., 11., 9., 10.),) * len(REPLAY)
    for context, candles in ((PRIMARY, primary_bars), (HIGHER, HIGHER_OHLC)):
        duration = int(context.timeframe) * 60_000
        for index, (opening, high, low, close) in enumerate(candles):
            start = index * duration
            preview = context == HIGHER and index == 1
            phases = (
                ('open', 0, opening, opening, opening),
                ('update', duration * 3 // 4 if preview else duration // 2, high, low, high if preview else close),
                ('close', duration, high, low, close),
            )
            for step, (kind, elapsed, event_high, event_low, price) in enumerate(phases):
                rows.append(MarketEvent(0, context.context_id, kind, start, start + elapsed, step,
                                        opening, event_high, event_low, price, float(step)))
    rows.sort(key=lambda e: (e.event_time, 2 if e.kind == 'open' else e.context_id == PRIMARY.context_id))
    return tuple(replace(event, sequence=index) for index, event in enumerate(rows))


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path,
                          bars='bars.json', expected=expected))

    def output(base, qualifier, values):
        return qualifier, dict(title='', type=base, values=values)

    def executed(*outputs):
        qualifiers, rows = zip(*outputs, strict=True)
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=qualifiers, outputs=rows)

    def rejected(code):
        return dict(status='rejected', category='LANGUAGE_INVALID', code=code)

    for version in range(1, 7):
        prefix = '' if version < 5 else 'math.'
        avg = prefix + 'avg'
        min_ = prefix + 'min'
        max_ = prefix + 'max'
        scalar_avg = 'series' if version < 5 else 'simple'
        add(version, 'average',
            f'plot({avg}(1,2.0,6))\nplot({avg}(0,2,4,6,8,10))\nplot({avg}(close,1,2.0))\n'
            f'plot({avg}(bar_index==1?na:close,2))',
            executed(output('float', scalar_avg, (3.,) * 4), output('float', scalar_avg, (5.,) * 4),
                     output('float', 'series', (3., 11 / 3, 13 / 3, 5.)),
                     output('float', 'series', (4., None, 6., 7.))))
        add(version, 'extrema',
            f'plot({min_}(3,2))\nplot({max_}(3,2.5))\n'
            f'plot({min_}(9,int(close),5))\nplot({max_}(7,close,8.5))\nplot({min_}(na,2,3))',
            executed(output('int', 'const', (2,) * 4), output('float', 'const', (3.,) * 4),
                     output('int', 'series', (5, 5, 5, 5)), output('float', 'series', (8.5, 8.5, 10., 12.)),
                     output('int', 'const', (None,) * 4)))
        arity = 7 if version < 4 else 33
        add(version, 'average-arity', f'plot({avg}({",".join(["1"] * arity)}))',
            rejected('NO_MATCHING_OVERLOAD'))
        if version >= 4:
            numbers = ','.join(map(str, range(1, 33)))
            add(version, 'wide-average', f'plot({avg}({numbers}))',
                executed(output('float', scalar_avg, (16.5,) * 4)))

    bars = [dict(time=i * 60_000, open=value, high=value, low=value, close=value, volume=2.)
            for i, value in enumerate(CLOSES)]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2',
        context_id=PRIMARY.context_id, symbol=PRIMARY.symbol, timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-calculated means, clipping, type promotion and positional arity limits.'))) + b'\n'
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
    print('Numeric aggregate fixtures verified.')


if __name__ == '__main__':
    main()
