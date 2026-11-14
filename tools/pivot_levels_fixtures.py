"""Manual period opens, OHLC formulas and a replay tape crossing daily R1."""

import argparse

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/pivot-levels'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/pivot-levels/daily-levels.pine'
TITLE = 'Pivot levels'
DAY = 86_400_000
SAMPLES = tuple((day * DAY + (i + 1) * 60_000, float(price))
                for day, prices in enumerate(((100, 120, 80), (100, 90, 95), (103, 98, 105)))
                for i, price in enumerate(prices))
ALERT = (26, 'Daily R1 breakout at 100')


def source(version, body):
    head = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{head}("{TITLE}")\n{body}\n'


def tape(context_id):
    for index, (time, price) in enumerate(SAMPLES):
        for revision, kind in enumerate(('open', 'update', 'close')):
            yield MarketEvent(index * 3 + revision, context_id, kind, time, time + revision * 30_000,
                              revision, price, price, price, price, float(revision))


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
                    observation_count=len(SAMPLES), qualifiers=['series'] * len(outputs),
                    outputs=[dict(title='', type=base, values=values) for base, values in outputs])

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 5):
        add(version, 'timeframe-unavailable', 'plot(timeframe.change("D") ? 1 : 0)', rejected('SIGNATURE_UNAVAILABLE'))
        add(version, 'levels-unavailable', 'ta.pivot_point_levels("Classic",true)', rejected('SIGNATURE_UNAVAILABLE'))
    for version in (5, 6):
        add(version, 'calendar', 'plot(timeframe.change("1D") ? 1 : 0)\n'
            'plot(timeframe.change(timeframe="1W") ? 1 : 0)\nplot(timeframe.change() ? 1 : 0)', executed(
                ('int', (1, 0, 0, 1, 0, 0, 1, 0, 0)), ('int', (1, 0, 0, 0, 0, 0, 0, 0, 0)), ('int', (1,) * 9)))
        add(version, 'anchored', 'a=ta.pivot_point_levels("Traditional",timeframe.change("D"))\n'
            'plot(array.get(a,0))\nplot(array.get(a,1))\nplot(array.get(a,2))', executed(
                ('float', (None,) * 3 + (280 / 3,) * 3 + (95.,) * 3),
                ('float', (None,) * 3 + (320 / 3,) * 3 + (100.,) * 3),
                ('float', (None,) * 3 + (200 / 3,) * 3 + (90.,) * 3)))
        add(version, 'developing', 'a=ta.pivot_point_levels(developing=true,type="Classic",anchor=false)\n'
            'plot(array.get(a,0))\nplot(array.get(a,9))', executed(
                ('float', (100., 340 / 3, 280 / 3, 100., 290 / 3, 295 / 3, 101., 298 / 3, 305 / 3)),
                ('float', (None,) * 9)))
        add(version, 'woodie-developing', 'plot(array.get(ta.pivot_point_levels("Woodie",true,true),0))',
            rejected('PIVOT_LEVEL_WOODIE', 'RUNTIME_ERROR'))
        add(version, 'unknown-type', 'plot(array.get(ta.pivot_point_levels("Other",true),0))',
            rejected('PIVOT_LEVEL_TYPE', 'RUNTIME_ERROR'))
        add(version, 'invalid-period', 'plot(timeframe.change("1S") ? 1 : 0)',
            rejected('TIMEFRAME_CHANGE_PERIOD', 'RUNTIME_ERROR'))
    bars = [dict(time=time, open=price, high=price, low=price, close=price, volume=2.) for time, price in SAMPLES]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='levels.test',
                                      symbol='TEST:LEVELS', timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='UTC period boundaries and hand-calculated OHLC levels.'))) + b'\n'
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
    print('Pivot level fixtures verified.')


if __name__ == '__main__':
    main()
