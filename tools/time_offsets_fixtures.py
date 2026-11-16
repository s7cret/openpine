"""Hand-calculated time offsets over missing candles and whole target periods."""

import argparse

from packages.contracts.wire import canonical
from tools.higher_timeframes_fixtures import bars, stamp
from tools.specification import ROOT


DIRECTORY = 'conformance/time-offsets'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/time-offsets/daily-boundaries.pine'
TITLE = 'Time offsets'
CHART = ('2024-02-25T23:00', '2024-02-26', '2024-02-26T01:00',
         '2024-02-29T23:00', '2024-03-01', '2024-03-04T03:00')
MARKERS = ('2024-03-01', '2024-03-02', '2024-03-06')
PAUSE_AFTER = 3


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def dates(*values):
    return tuple(stamp(value) if value is not None else None for value in values)


def fixtures():
    files = {'sparse.json': canonical(bars('60', dates(*CHART))) + b'\n'}
    cases = []

    def add(version, name, body, expected):
        case_id = f'v{version}-{name}'
        path = case_id + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=case_id, language_version=version, source=path,
                          bars='sparse.json', expected=expected))

    def executed(*rows):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(CHART), qualifiers=['series'] * len(rows),
                    outputs=[dict(title='', type='int', values=values) for values in rows])

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 5):
        add(version, 'chart-offset-unavailable', 'plot(time("", bars_back=1))', rejected('TIME_OFFSET_VERSION'))
    add(5, 'timeframe-offset-unavailable', 'plot(time_close("D", timeframe_bars_back=1))',
        rejected('TIME_OFFSET_VERSION'))
    for version in (5, 6):
        add(version, 'chart-offsets',
            'plot(time("", 1))\nplot(time_close("1D", "24x7", "UTC", -1))\nplot(time("D", bars_back=5000))',
            executed(dates(None, *CHART[:-1]),
                     dates('2024-02-27', '2024-02-27', '2024-02-27', '2024-03-02', '2024-03-02', '2024-03-05'),
                     (None,) * len(CHART)))
    add(6, 'observed-days', 'offset = bar_index > 2 ? 2 : 1\n'
        'plot(time("1D", timeframe_bars_back=offset))\n'
        'plot(bar_index >= 3 ? time_close("1D", "24x7", 0, 1) : int(na))',
        executed(dates(None, '2024-02-25', '2024-02-25', '2024-02-25', '2024-02-26', '2024-02-29'),
                 dates(None, None, None, '2024-02-27', '2024-03-01', '2024-03-02')))
    add(6, 'mixed-offsets', 'plot(time("D", bars_back=1, timeframe_bars_back=1))\n'
        'plot(time("D", bars_back=1, timeframe_bars_back=-1))\n'
        'plot(time_close("D", bars_back=-1, timeframe_bars_back=-1))\n'
        'plot(time("D", bars_back=-48, timeframe_bars_back=3))',
        executed(dates(None, None, '2024-02-25', '2024-02-25', '2024-02-26', '2024-02-29'),
                 dates(None, '2024-02-26', '2024-02-27', '2024-02-27', '2024-03-01', '2024-03-02'),
                 dates('2024-02-28', '2024-02-28', '2024-02-28', '2024-03-03', '2024-03-03', '2024-03-06'),
                 dates(None, '2024-02-25', '2024-02-25', '2024-02-26', '2024-02-29', '2024-03-01')))
    add(6, 'calendar-lengths', 'plot(time_close("1W", bars_back=-1))\n'
        'plot(time_close("1M", timeframe_bars_back=-1))',
        executed(dates(*(['2024-03-04'] * 5 + ['2024-03-11'])),
                 dates(*(['2024-04-01'] * 4 + ['2024-05-01'] * 2))))
    for name, expression, code in (
        ('chart-limit', 'time("", bars_back=5001)', 'TIME_OFFSET_RANGE'),
        ('target-limit', 'time_close("1D", timeframe_bars_back=-501)', 'TIME_OFFSET_RANGE'),
        ('forecast-session', 'time("", "2300-0100", bars_back=-1)', 'TIME_OFFSET_SESSION'),
        ('forecast-zone', 'time("", "24x7", "UTC+01", bars_back=-1)', 'SESSION_ZONE'),
    ):
        add(6, name, f'plot({expression})', rejected(code, 'RUNTIME_ERROR'))
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Observed chart candles and distinct target days; UTC hour, day, week and month boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / DIRECTORY
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, content in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == content, name
        else:
            path.write_bytes(content)
    print('Time offset fixtures verified.')


if __name__ == '__main__':
    main()
