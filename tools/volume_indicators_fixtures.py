"""Hand-calculated volume series and VWAP resets for each admitted version."""

import argparse

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT

DIRECTORY = 'conformance/volume-indicators'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/volume-indicators/anchored-vwap.pine'
CLOSES = (10., 14., 12., 16., 8., 10., 20., 18.)
VOLUMES = (2., 1., 0., 3., None, 2., 4., 1.)
DAILY = (10., 11.3333, 11.3333, 13.6667, None, 10., 16.6667, 16.8571)
TYPICAL = (10.3333, 11.6667, 11.6667, 14., None, 10.3333, 17., 17.1905)
ACCUMULATION = (-.6667, -1., -1., -2., -2., -2.6667, -4., -4.3333)
OBV = (None, 1., 1., 4., 4., 6., 10., 9.)
PVT = (None, .4, .4, 1.4, 1.4, 1.9, 5.9, 5.8)
MFI = (None, None, None, 100., 100., 100., 100., 81.6327)
ANCHORED = (None, 14., 14., 15.5, None, 10., 20., 19.6)
UPPER = (None, 14., 14., 16.366, None, 10., 20., 20.4)
LOWER = (None, 14., 14., 14.634, None, 10., 20., 18.8)


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Volume indicators")\n{body}\n'


def anchored_tape(context_id):
    for index, (close, volume) in enumerate(zip(CLOSES, VOLUMES, strict=True)):
        time = index * 60_000
        volume = volume if volume is not None else 2.
        yield MarketEvent(index * 3, context_id, 'open', time, time, 0, close, close, close, close, 0.)
        yield MarketEvent(index * 3 + 1, context_id, 'update', time, time + 30_000, 1,
                          close, close + 2, close - 1, close - 1, volume / 2)
        yield MarketEvent(index * 3 + 2, context_id, 'close', time, time + 60_000, 2,
                          close, close + 2, close - 1, close, volume)


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        case_id = f'v{version}-{name}'
        path = case_id + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=case_id, language_version=version, source=path,
                          bars='bars.json', expected=expected))

    def executed(*outputs):
        return dict(status='executed', title='Volume indicators', shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=['series'] * len(outputs),
                    outputs=[dict(title='', type='float', values=list(values)) for values in outputs])

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 7):
        prefix = 'ta.' if version >= 5 else ''
        rounding = 'math.round' if version >= 5 else 'round'

        def plots(*expressions):
            return '\n'.join(f'plot({rounding}(({expr})*10000)/10000.0)' for expr in expressions)

        add(version, 'variables', plots(prefix + 'accdist', prefix + 'vwap'), executed(ACCUMULATION, TYPICAL))
        if version <= 5:
            add(version, 'daily-vwap', plots(prefix + 'vwap(close)'), executed(DAILY))
        else:
            add(version, 'unconfirmed-daily-vwap', 'ta.vwap(close)', rejected('NO_MATCHING_OVERLOAD'))
        if version >= 4:
            add(version, 'flows', 'n=bar_index%3+1\n' + plots(
                prefix + 'obv', prefix + 'pvt', prefix + 'mfi(length=n,series=close)'), executed(OBV, PVT, MFI))
        else:
            add(version, 'version-mfi', 'mfi(close,2)',
                rejected('SIGNATURE_UNAVAILABLE', 'SPEC_UNRESOLVED' if version == 2 else 'LANGUAGE_INVALID'))
        if version >= 5:
            add(version, 'anchored-bands',
                'reset=bar_index==1 or bar_index==4 or bar_index==6\n'
                '[mid,upper,lower]=ta.vwap(stdev_mult=1.0,source=close,anchor=reset)\n'
                + plots('ta.vwap(close,reset)', 'mid', 'upper', 'lower'),
                executed(ANCHORED, ANCHORED, UPPER, LOWER))
        else:
            add(version, 'version-anchor', 'vwap(close,true)', rejected('TOO_MANY_ARGUMENTS'))

    bars = []
    for i, (c, v) in enumerate(zip(CLOSES, VOLUMES, strict=True)):
        bars.append(dict(time=86_160_000 + i * 60_000, open=c,
                         high=c if i == 2 else c + 2, low=c if i == 2 else c - 1, close=c, volume=v))
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='volume.test',
                                      symbol='TEST:VOLUME', timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-calculated signed flows, daily and explicit resets, and volume-weighted moments.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / DIRECTORY
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Volume indicator fixtures verified.')


if __name__ == '__main__':
    main()
