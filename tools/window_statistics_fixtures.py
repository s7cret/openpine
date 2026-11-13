"""Hand-calculated rolling statistics and version boundaries."""

import argparse

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT

DIRECTORY = 'conformance/window-statistics'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/window-statistics/percentile-channel.pine'
CLOSES = (2., 4., 2., 8., 3., 7., 2., 10.)
BREAKOUTS = (23,)


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Window statistics")\n{body}\n'


def breakout_tape(context_id):
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
        return dict(status='executed', title='Window statistics', shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=['series'] * len(outputs),
                    outputs=[dict(title='', type=base, values=values) for base, values in outputs])

    def rejected(code):
        return dict(status='rejected', category='LANGUAGE_INVALID', code=code)

    for version in range(1, 7):
        prefix = 'ta.' if version >= 5 else ''
        rounding = 'math.round' if version >= 5 else 'round'
        rank = f'{rounding}({prefix}percentrank(close,3)*100)/100.0'
        nearest = f'{prefix}percentile_nearest_rank'
        linear = f'{prefix}percentile_linear_interpolation'
        add(version, 'ranks', f'plot({nearest}(close,3,0))\nplot({nearest}(close,3,100))\n'
            f'plot({linear}(close,3,25))\nplot({rank})', executed(
                ('float', (None, None, 2., 2., 2., 3., 2., 2.)),
                ('float', (None, None, 4., 8., 8., 8., 7., 10.)),
                ('float', (None, None, 2., 3., 2.5, 5., 2.5, 4.5)),
                ('float', (None, None, None, 100., 33.33, 66.67, 0., 100.))))
        add(version, 'percentage-qualifier', f'{linear}(close,3,bar_index)', rejected('ARGUMENT_QUALIFIER'))
        if version < 4:
            add(version, 'version-median', 'median(close,2)', rejected('SIGNATURE_UNAVAILABLE'))
            add(version, 'version-mode', 'mode(close,2)', rejected('SIGNATURE_UNAVAILABLE'))
            add(version, 'length-qualifier', f'{nearest}(close,bar_index+1,50)', rejected('ARGUMENT_QUALIFIER'))
        else:
            add(version, 'changing-window', 'n=bar_index%3+2\n'
                f'plot({prefix}median(length=n,source=close))\nplot({prefix}mode(close,n))\n'
                f'plot({prefix}median(bar_index%5-3,n))\nplot({prefix}mode(bar_index%5-3,n))', executed(
                    ('float', (None, None, None, 5., 3., 5., 4.5, 7.)),
                    ('float', (None, None, None, 2., 2., 2., 2., 2.)),
                    ('int', (None, None, None, 0, 0, 0, -2, -2)),
                    ('int', (None, None, None, -1, -1, -3, -3, -3))))
    bars = [dict(time=i * 60_000, open=c, high=c + 1, low=c - 1, close=c, volume=2.)
            for i, c in enumerate(CLOSES)]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='statistics.test',
                                      symbol='TEST:STATISTICS', timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-sorted price windows, ranks against preceding observations and typed medians.'))) + b'\n'
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
    print('Window statistics fixtures verified.')


if __name__ == '__main__':
    main()
