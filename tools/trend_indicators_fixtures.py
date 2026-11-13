"""Independently calculated outcomes for trend reversals and version gates."""

import argparse

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT

DIRECTORY = 'conformance/trend-indicators'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/trend-indicators/trend-reversal.pine'
CLOSES = (10., 11., 20., 18., 8., 9., 25.)
SAR = (None, 9., 9., 10., 21., 19., 7.)
SUPERTREND = (11., 11., 15., 15., 13.5, 10., 16.5)
DIRECTION = (1., 1., -1., -1., 1., 1., -1.)


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Trends")\n{body}\n'


def reversal_tape(context_id):
    for index, close in enumerate(CLOSES):
        time = index * 60_000
        for revision, kind in enumerate(('open', 'update', 'close')):
            high, low = (close, close) if revision == 0 else (close + 1, close - 1)
            yield MarketEvent(index * 3 + revision, context_id, kind, time, time + revision * 30_000,
                              revision, close, high, low, high if revision == 1 else close, float(revision))


def fixtures():
    files, cases = {}, []

    def add(v, name, body, expected):
        case_id = f'v{v}-{name}'
        path = case_id + '.pine'
        files[path] = source(v, body).encode()
        cases.append(dict(case_id=case_id, language_version=v, source=path, bars='bars.json', expected=expected))

    def executed(*outputs):
        return dict(status='executed', title='Trends', shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=['series'] * len(outputs),
                    outputs=[dict(title='', type='float', values=list(values)) for values in outputs])

    def rejected(code):
        return dict(status='rejected', category='LANGUAGE_INVALID', code=code)

    for v in range(1, 7):
        p = 'ta.' if v >= 5 else ''
        add(v, 'sar', f'plot({p}sar(0.25,0.25,0.5))', executed(SAR))
        if v < 4:
            for name, args in (('dmi', '1,1'), ('supertrend', '1,1')):
                add(v, 'version-' + name, f'{name}({args})', rejected('SIGNATURE_UNAVAILABLE'))
        else:
            rounding = 'math.round' if v >= 5 else 'round'
            add(v, 'dmi', f'[p,m,a] = {p}dmi(adxSmoothing=1,diLength=1)\n'
                f'plot({rounding}(p*100)/100.0)\nplot({rounding}(m*100)/100.0)\nplot(a)',
                executed((None, 50., 90., 0., 0., 50., 94.12),
                         (None, 0., 0., 66.67, 90.91, 0., 0.), (None,) + (100.,) * 6))
            add(v, 'supertrend', f'f = bar_index == 3 ? 1.0 : 0.5\n'
                f'[t,d] = {p}supertrend(atrPeriod=1,factor=f)\nplot(t)\nplot(d)', executed(SUPERTREND, DIRECTION))
            add(v, 'series-period', f'{p}dmi(bar_index + 1,1)', rejected('ARGUMENT_QUALIFIER'))
    bars = [dict(time=i * 60_000, open=c, high=c + 1, low=c - 1, close=c, volume=2.)
            for i, c in enumerate(CLOSES)]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='trends.test',
                                      symbol='TEST:TRENDS', timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Manual True Range and price moves, hand-traced bands and SAR reversals.'))) + b'\n'
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
    print('Trend indicator fixtures verified.')


if __name__ == '__main__':
    main()
