"""Closed-form expectations and a replay tape for weighted indicators."""

import argparse
from dataclasses import replace

from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/weighted-indicators'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/weighted-indicators/price-filters.pine'
TITLE = 'Weighted windows'
CLOSES = (2., 4., 6., 8., 10., 12., 14., 16.)
HIGHER_CLOSES = (2., 4., 14., 2.)
PRIMARY = MarketContext('weighted-primary', 'TEST:WEIGHTED', '1', 'recorded.1')
HIGHER = MarketContext('weighted-higher', PRIMARY.symbol, '2', PRIMARY.source)
ALMA = (None, 3., 5., 7., 9., 11., 13., 15.)
HMA = (None,) * 5 + (38 / 3, 44 / 3, 50 / 3)
HISTORICAL_COG = (None, None, None, -4 / 3, -4 / 3, -11 / 9, -11 / 9, -15 / 8)
REPLAY_COG = (None, None, -4 / 3, -4 / 3, -11 / 9, -11 / 9, -15 / 8, -15 / 8)
ALERT = (31, 'COG below threshold: -1.875')


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}")\n{body}\n'


def tape():
    rows = []
    for context, samples in ((PRIMARY, CLOSES), (HIGHER, HIGHER_CLOSES)):
        duration = int(context.timeframe) * 60_000
        for index, close in enumerate(samples):
            start = index * duration
            for step, kind in enumerate(('open', 'update', 'close')):
                rows.append(MarketEvent(0, context.context_id, kind, start, start + step * duration // 2,
                                        step, close, close, close, close, float(step)))
    rows.sort(key=lambda event: (event.event_time, 2 if event.kind == 'open' else event.context_id == PRIMARY.context_id))
    return tuple(replace(row, sequence=i) for i, row in enumerate(rows))


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        case = dict(case_id=identifier, language_version=version, source=path, bars='bars.json', expected=expected)
        if expected['status'] == 'executed':
            case['numeric_tolerance'] = dict(absolute=1.e-12, relative=1.e-12)
        cases.append(case)

    def executed(*outputs):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=['series'] * len(outputs),
                    outputs=[dict(title='', type='float', values=values) for values in outputs])

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    ramp_cog = (None, *(-(3 * i + 1) / (2 * i + 1) for i in range(1, len(CLOSES))))
    # exp(-1/2) rounded independently to binary64 for the two-weight oracle.
    floor_alma = (None, *(x - 2 / (1 + 0.6065306597126334) for x in CLOSES[1:]))
    for version in range(1, 7):
        ns = 'ta.' if version >= 5 else ''
        alma, cog, hma = (ns + name for name in ('alma', 'cog', 'hma'))
        add(version, 'baseline', f'plot({alma}(close,2,0.5,2))\nplot({cog}(close,2))', executed(ALMA, ramp_cog))
        add(version, 'changing-alma', f'plot({alma}(close,bar_index%2+2,0.5,2))',
            rejected('ARGUMENT_QUALIFIER') if version < 4 else executed((None, None, 5., 6., 9., 10., 13., 14.)))
        add(version, 'changing-cog', f'plot({cog}(close,bar_index%2+2))',
            rejected('ARGUMENT_QUALIFIER') if version < 4 else
            executed((None, None, -7 / 5, -16 / 9, -13 / 9, -28 / 15, -19 / 13, -40 / 21)))
        add(version, 'alma-floor', f'plot({alma}(close,2,0.5,2,true))',
            rejected('TOO_MANY_ARGUMENTS') if version < 4 else executed(floor_alma))
        if version < 4:
            add(version, 'hma-unavailable', 'plot(hma(close,5))', rejected('SIGNATURE_UNAVAILABLE'))
        else:
            add(version, 'hma', f'plot({hma}(length=5,source=close))\nplot({hma}(close,1))', executed(HMA, CLOSES))
            add(version, 'changing-hma', f'plot({hma}(close,bar_index%2+2))', rejected('ARGUMENT_QUALIFIER'))

    add(6, 'bad-sigma', 'plot(ta.alma(close,2,0.5,0))', rejected('ALMA_SIGMA', 'RUNTIME_ERROR'))
    add(6, 'bad-length', 'plot(ta.cog(close,0))', rejected('WINDOW_LENGTH', 'RUNTIME_ERROR'))
    add(6, 'holes', 'src=bar_index==2?na:close\nplot(ta.alma(src,2,0.5,2))\nplot(ta.hma(src,2))\n'
        'plot(ta.cog(src,2))\nplot(ta.cog(close-close,2))',
        executed((None, 3., None, None, 9., 11., 13., 15.), (None, 14 / 3, 14 / 3, 28 / 3, 32 / 3, 38 / 3, 44 / 3, 50 / 3),
                 (None, -4 / 3, -4 / 3, -4 / 3, *ramp_cog[4:]), (None,) * 8))
    bars = [dict(time=i * 60_000, open=x, high=x, low=x, close=x, volume=2.) for i, x in enumerate(CLOSES)]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id=PRIMARY.context_id,
                                       symbol=PRIMARY.symbol, timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Two Gaussian weights and closed-form ramp and hole expectations for weighted sums.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    directory = ROOT / DIRECTORY
    check = parser.parse_args(argv).check
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, data in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == data, name
        else:
            path.write_bytes(data)
    print('Weighted indicator fixtures verified.')


if __name__ == '__main__':
    main()
