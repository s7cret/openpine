"""Independent price sequences, range expectations and a two-context replay."""

import argparse
from dataclasses import replace

from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/extrema'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/extrema/range-breakout.pine'
TITLE = 'Range and extrema'
CLOSES = (6., 4., 6., 5., 5., 10., 8., 9.)
HIGHS = (7., 6., 9., 8., 7., 12., 10., 11.)
LOWS = (3., 2., 4., 3., 4., 9., 7., 8.)
HIGHER_CLOSES = (4., 9., 5., 12.)
PRIMARY = MarketContext('extrema-primary', 'TEST:EXTREMA', '1', 'recorded.1')
HIGHER = MarketContext('extrema-higher', PRIMARY.symbol, '2', PRIMARY.source)
PEAK = (6., 6., 6., 6., 6., 10., 10., 10.)
TROUGH = (6., 4., 4., 4., 4., 4., 4., 4.)
HISTORICAL_RANGE = (None, None, None, 5., 5., 4., 4., 7.)
REPLAY_RANGE = (None, None, 5., 5., 4., 4., 7., 7.)
ALERT = (26, 'New maximum: 10, range: 4')


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def tape():
    rows = []
    for context, samples in ((PRIMARY, CLOSES), (HIGHER, HIGHER_CLOSES)):
        duration = int(context.timeframe) * 60_000
        for index, close in enumerate(samples):
            start = index * duration
            preview = 30. if context == PRIMARY and index == 4 else close
            for step, kind, price, high in ((0, 'open', close, close), (1, 'update', preview, preview),
                                            (2, 'close', close, preview)):
                rows.append(MarketEvent(0, context.context_id, kind, start, start + step * duration // 2,
                                        step, close, high, close, price, float(step)))
    rows.sort(key=lambda e: (e.event_time, 2 if e.kind == 'open' else e.context_id == PRIMARY.context_id))
    return tuple(replace(event, sequence=i) for i, event in enumerate(rows))


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path, bars='bars.json', expected=expected))

    def executed(*outputs):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=['series'] * len(outputs),
                    outputs=[dict(title='', type=base, values=values) for base, values in outputs])

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 7):
        ns = 'ta.' if version >= 5 else ''
        math = 'math.' if version >= 5 else ''
        add(version, 'window-extrema', '\n'.join(f'plot({ns}{expr})' for expr in
            ('highest(close,2)', 'lowest(close,2)', 'highest(2)', 'lowest(2)')),
            executed(('float', (6., 6., 6., 6., 5., 10., 10., 9.)), ('float', (6., 4., 4., 5., 5., 5., 8., 8.)),
                     ('float', (7., 7., 9., 9., 8., 12., 12., 11.)), ('float', (3., 2., 2., 3., 3., 4., 7., 7.))))
        add(version, 'argument-extrema', f'plot({math}max(close,7.0))\nplot({math}min(close,5.0))',
            executed(('float', (7., 7., 7., 7., 7., 10., 8., 9.)), ('float', (5., 4., 5., 5., 5., 5., 5., 5.))))
        if version < 4:
            add(version, 'range-unavailable', 'plot(range(close,2))', rejected('SIGNATURE_UNAVAILABLE'))
        else:
            add(version, 'range', f'plot({ns}range(close,2))\nplot({ns}range(length=2,source=int(close)))',
                executed(('float', (None, 2., 2., 1., 0., 5., 2., 1.)), ('int', (None, 2, 2, 1, 0, 5, 2, 1))))
            add(version, 'dynamic-holes', f'src=bar_index%3==1?na:close\nplot({ns}range(src,bar_index%2+2))',
                executed(('float', (None, None, 0., 1., 1., 5., 2., 5.))))
            add(version, 'invalid-length', f'plot({ns}range(close,0))', rejected('WINDOW_LENGTH', 'RUNTIME_ERROR'))
        if version < 5:
            add(version, 'cumulative-unavailable', 'plot(ta.max(close))', rejected('SIGNATURE_UNAVAILABLE'))
        else:
            add(version, 'cumulative', 'src=bar_index%5==0?na:close\nplot(ta.max(src))\nplot(ta.min(source=src))',
                executed(('float', (None, 4., 6., 6., 6., 6., 8., 9.)), ('float', (None, 4., 4., 4., 4., 4., 4., 4.))))
    bars = [dict(time=i * 60_000, open=c, high=h, low=l, close=c, volume=2.)
            for i, (c, h, l) in enumerate(zip(CLOSES, HIGHS, LOWS, strict=True))]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id=PRIMARY.context_id,
                                       symbol=PRIMARY.symbol, timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Manual extrema of each window and prefix on fixed price sequences.'))) + b'\n'
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
    print('Extrema fixtures verified.')


if __name__ == '__main__':
    main()
