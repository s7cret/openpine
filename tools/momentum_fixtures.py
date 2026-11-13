"""Hand-derived window and oscillator values for supported Pine versions."""

import argparse

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT

DIRECTORY = 'conformance/momentum'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/momentum/cci-crossing.pine'
CLOSES = (10., 12., 12., 9., 8., 14., 11., 11., 15.)
FIXED_DEV = (None, None, .8889, 1.3333, 1.5556, 2.4444, 2., 1.3333, 1.7778)
FIXED_CCI = (None, None, 50., -100., -71.4286, 100., 0., -50., 100.)
TSI_SEEDED = (None, None, None, None, -.3333, .5472, .1329, .0397, .5102)
TSI_FIRST = (None, 1., 1., -.125, -.4651, .4684, .094, .0085, .4929)
DYNAMIC_DEV = (None, None, None, 1.5, 1.5556, 2.25, 1.5, 1.3333, 1.75)
DYNAMIC_CCI = (None, None, None, -66.6667, -71.4286, 96.2963, -66.6667, -50., 85.7143)
DYNAMIC_CMO = (None, None, None, -100., -100., 20., 33.3333, 33.3333, 53.8462)
DYNAMIC_WPR = (-50., -25., -25., -80., -83.3333, -12.5, -80., -80., -16.6667)


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Momentum")\n{body}\n'


def crossing_tape(context_id):
    for index, close in enumerate(CLOSES):
        time = index * 60_000
        for revision, kind in enumerate(('open', 'update', 'close')):
            high, low = (close, close) if revision == 0 else (close + 1, close - 1)
            yield MarketEvent(index * 3 + revision, context_id, kind, time, time + revision * 30_000,
                              revision, close, high, low, low if revision == 1 else close, float(revision))


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        case_id = f'v{version}-{name}'
        path = case_id + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=case_id, language_version=version, source=path,
                          bars='bars.json', expected=expected))

    def executed(*outputs):
        return dict(status='executed', title='Momentum', shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=['series'] * len(outputs),
                    outputs=[dict(title='', type='float', values=list(values)) for values in outputs])

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 7):
        prefix = 'ta.' if version >= 5 else ''
        rounding = 'math.round' if version >= 5 else 'round'

        def plots(*expressions):
            return '\n'.join(f'plot({rounding}({prefix}{expr}*10000)/10000.0)' for expr in expressions)

        add(version, 'deviation', plots('dev(close,3)', 'cci(close,3)'), executed(FIXED_DEV, FIXED_CCI))
        tsi = 'tsi(close,2,3)' if version < 3 else 'tsi(long_length=3,source=close,short_length=2)'
        add(version, 'tsi', plots(tsi), executed(TSI_SEEDED if version < 5 else TSI_FIRST))
        add(version, 'tsi-series-period', f'{prefix}tsi(close,bar_index+1,3)', rejected('ARGUMENT_QUALIFIER'))
        if version < 4:
            add(version, 'version-cmo', 'cmo(close,3)', rejected('SIGNATURE_UNAVAILABLE'))
            category = 'SPEC_UNRESOLVED' if version == 2 else 'LANGUAGE_INVALID'
            add(version, 'version-wpr', 'wpr(3)', rejected('SIGNATURE_UNAVAILABLE', category))
            add(version, 'series-length', 'dev(close,bar_index+1)', rejected('ARGUMENT_QUALIFIER'))
        else:
            add(version, 'changing-length', 'n=bar_index%3+2\n' + plots(
                'dev(length=n,source=close)', 'cci(close,n)', 'cmo(length=n,series=close)', 'wpr(length=n)'),
                executed(DYNAMIC_DEV, DYNAMIC_CCI, DYNAMIC_CMO, DYNAMIC_WPR))

    bars = [dict(time=i * 60_000, open=c, high=c + 1, low=c - 1, close=c, volume=2.)
            for i, c in enumerate(CLOSES)]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='momentum.test',
                                      symbol='TEST:MOMENTUM', timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Mean absolute deviations, signed changes, two EMA passes and extrema calculated independently.'))) + b'\n'
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
    print('Momentum fixtures verified.')


if __name__ == '__main__':
    main()
