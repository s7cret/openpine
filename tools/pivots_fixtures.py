"""Independent swing-pivot observations and the example replay tape."""

import argparse

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/pivots'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/pivots/confirmed-swings.pine'
TITLE = 'Swing pivots'
CLOSES = (4., 9., 8., 3., 7., 7., 2., 8.)
ALERTS = ((8, 2, 'Swing high 9'), (14, 4, 'Swing low 3'))
LABELS = ((1, 9., 'Swing high'), (3, 3., 'Swing low'))


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}")\n{body}\n'


def swing_tape(context_id):
    for index, prices in enumerate(((4., 4., 4.), (9., 9., 9.), (6., 10., 8.), (3., 3., 3.), (7., 7., 7.))):
        time = index * 60_000
        for revision, (kind, price) in enumerate(zip(('open', 'update', 'close'), prices, strict=True)):
            seen = prices[:revision + 1]
            yield MarketEvent(index * 3 + revision, context_id, kind, time, time + revision * 30_000,
                              revision, prices[0], max(seen), min(seen), price, float(revision))


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path,
                          bars='bars.json', expected=expected))

    def executed(*outputs):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=['series'] * len(outputs),
                    outputs=[dict(title='', type='float', values=values) for values in outputs])

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 7):
        prefix = 'ta.' if version >= 5 else ''
        high, low = prefix + 'pivothigh', prefix + 'pivotlow'
        add(version, 'swings', f'plot({high}(close,1,1))\nplot({low}(close,1,1))\n'
            f'plot({high}(1,1))\nplot({low}(1,1))', executed(
                (None, None, 9., None, None, None, None, None),
                (None, None, None, None, 3., None, None, 2.),
                (None, None, 10., None, None, None, None, None),
                (None, None, None, None, 2., None, None, 1.)))
        add(version, 'invalid-sum', f'plot({low}(close,100000,1))',
            rejected('PIVOT_LENGTH', 'RUNTIME_ERROR'))
        if version < 3:
            add(version, 'length-qualifier', f'plot({high}(close,1,bar_index%2))',
                rejected('NO_MATCHING_OVERLOAD'))
        else:
            add(version, 'changing-legs', 'left=bar_index%2\nright=1-bar_index%2\n'
                f'plot({high}(rightbars=right,source=close,leftbars=left))\n'
                f'plot({low}(leftbars=left,rightbars=right))', executed(
                    (None, 9., 9., None, None, None, 7., 8.),
                    (None, None, None, 2., 2., None, None, None)))
        if version >= 5:
            add(version, 'whole-float-legs', f'plot({high}(0.0,0.0))\nplot({low}(close,0.0,0.0))',
                executed(tuple(value + 1 for value in CLOSES), CLOSES))
            add(version, 'fractional-leg', f'plot({high}(0,1.5))',
                rejected('PIVOT_LENGTH', 'RUNTIME_ERROR'))

    bars = [dict(time=i * 60_000, open=c, high=c + 1, low=c - 1, close=c, volume=2.)
            for i, c in enumerate(CLOSES)]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='pivots.test',
                                      symbol='TEST:PIVOTS', timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-compared neighbours with asymmetric legs, tied prices and versioned input limits.'))) + b'\n'
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
    print('Pivot fixtures verified.')


if __name__ == '__main__':
    main()
