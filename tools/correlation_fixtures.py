"""Independent expectations for Pearson correlation and ranks."""

import argparse

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/correlation'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/correlation/price-volume.pine'
TITLE = 'Correlation'
CLOSES = (1., 2., 3., 4., 5., 6., 5., 4.)
VOLUMES = (1., 2., 3., 4., 3., 2., 1., 0.)
PEARSON = (None, None, 1., 1., 0., -1., 0., 1.)
RANKED = (None, None, 100., 100., 100., 100., 0., -100.)
ALERT = (17, 'Correlation below zero: -1')


def source(version, body):
    head = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{head}("{TITLE}")\n{body}\n'


def tape(context_id):
    for index, (close, volume) in enumerate(zip(CLOSES, VOLUMES, strict=True)):
        timestamp = index * 60_000
        for revision, kind in enumerate(('open', 'update', 'close')):
            yield MarketEvent(index * 3 + revision, context_id, kind, timestamp, timestamp + revision * 30_000,
                              revision, close, close, close, close, volume * revision / 2)


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
        function = 'ta.correlation' if version >= 5 else 'correlation'
        add(version, 'pearson', f'plot({function}(close,volume,3))\nplot({function}(close,5,3))',
            executed(PEARSON, (None,) * 8))
        add(version, 'bad-length', f'plot({function}(close,volume,0))', rejected('WINDOW_LENGTH', 'RUNTIME_ERROR'))
        if version < 4:
            add(version, 'changing-length', f'plot({function}(close,volume,bar_index%2+2))',
                rejected('ARGUMENT_QUALIFIER'))
        else:
            a, b = ('source_a', 'source_b') if version == 4 else ('source1', 'source2')
            add(version, 'changing-length', f'plot({function}(length=bar_index%2+2,{b}=volume,{a}=close))',
                executed((None, None, 1., 1., -1., -1., 1., 1.)))
        if version < 6:
            add(version, 'rci-unavailable', 'plot(ta.rci(close,3))', rejected('SIGNATURE_UNAVAILABLE'))
        else:
            add(version, 'rci', 'plot(ta.rci(close,3))\nplot(ta.rci(5,3))\nplot(ta.rci(close,1))',
                executed(RANKED, (None,) * 8, (None,) * 8))
            add(version, 'rci-length', 'plot(ta.rci(close,bar_index%2+2))', rejected('ARGUMENT_QUALIFIER'))
    bars = [dict(time=i * 60_000, open=price, high=price, low=price, close=price, volume=volume)
            for i, (price, volume) in enumerate(zip(CLOSES, VOLUMES, strict=True))]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='correlation.test',
                                      symbol='TEST:CORRELATION', timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Identical, reflected and orthogonal centered samples and manually ranked closes.'))) + b'\n'
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
    print('Correlation fixtures verified.')


if __name__ == '__main__':
    main()
