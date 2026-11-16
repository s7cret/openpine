"""Independent open/close expectations for the UTC chart and higher periods."""

import argparse
from datetime import UTC, datetime

from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/higher-timeframes'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/higher-timeframes/period-entry.pine'
TITLE = 'Higher timeframes'
HOUR = 3_600_000
DEFAULT = '1D'
OVERRIDE = '1W'
WINDOW = '0000-0100'
DEFAULT_ORDERS = (1, 25)
OVERRIDE_ORDERS = (1,)
PAUSE_AFTER = 1


def stamp(text):
    return int(datetime.fromisoformat(text).replace(tzinfo=UTC).timestamp()) * 1000


START = stamp('2024-02-26')
END = stamp('2024-02-27T01:00')


def bars(period, opened):
    return dict(schema_version='openpine.historical-bars.2', context_id='higher-primary',
                symbol='TEST:HIGHER', timeframe=period, bars=[
                    dict(time=value, open=10., high=11., low=9., close=10., volume=10.) for value in opened])


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def fixtures():
    chart_times = ('2024-02-25T23:00', '2024-02-26', '2024-02-26T01:00',
                   '2024-02-29T23:00', '2024-03-01', '2024-03-04T03:00')
    datasets = {
        'hourly': bars('60', map(stamp, chart_times)),
        'two-hour': bars('120', (stamp('2024-02-26'), stamp('2024-02-26T02:00'))),
        'seven-minute': bars('7', (0, 7 * 60_000)),
        'daily': bars('D', (stamp('2024-02-29'), stamp('2024-03-01'))),
        'weekly': bars('W', (stamp('2024-02-26'), stamp('2024-03-04'))),
        'monthly': bars('M', (stamp('2024-02-01'), stamp('2024-03-01'))),
        'strategy': bars('60', (START + i * HOUR for i in range(-1, 26))),
    }
    files = {key + '.json': canonical(value) + b'\n' for key, value in datasets.items()}
    cases = []

    def add(version, name, body, expected, sample='hourly'):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path,
                          bars=sample + '.json', expected=expected))

    def executed(*values, qualifiers=None):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(values[0]), qualifiers=qualifiers or ['series'] * len(values),
                    outputs=[dict(title='', type='int', values=row) for row in values])

    def date_rows(*dates):
        return tuple(stamp(value) if value is not None else None for value in dates)

    daily = date_rows('2024-02-25', '2024-02-26', '2024-02-26', '2024-02-29', '2024-03-01', '2024-03-04')
    weekly = date_rows('2024-02-19', '2024-02-26', '2024-02-26', '2024-02-26', '2024-02-26', '2024-03-04')
    monthly = date_rows(*(['2024-02-01'] * 4 + ['2024-03-01'] * 2))
    intraday = date_rows('2024-02-25T22:00', '2024-02-26', '2024-02-26',
                         '2024-02-29T22:00', '2024-03-01', '2024-03-04T02:00')
    daily_close = date_rows('2024-02-26', '2024-02-27', '2024-02-27', '2024-03-01', '2024-03-02', '2024-03-05')
    month_close = date_rows(*(['2024-03-01'] * 4 + ['2024-04-01'] * 2))
    for version in range(1, 7):
        add(version, 'opens', 'plot(time("120"))\nplot(time("D"))\nplot(time("1W"))\nplot(time("1M"))',
            executed(intraday, daily, weekly, monthly))
        if version >= 4:
            add(version, 'closes', 'plot(time_close("1D"))\nplot(time_close("M"))\nplot(time_close("1D")[1])',
                executed(daily_close, month_close, (None, *daily_close[:-1])))
        if version >= 5:
            add(version, 'anchors-and-gaps', 'start = time("1D")\npreviousEnd = time_close("1D")[1]\n'
                'plot(timeframe.change("1D") ? 1 : 0)\nplot(not na(start[1]) and start != start[1] ? 1 : 0)\n'
                'plot(not na(previousEnd) and start > previousEnd ? 1 : 0)',
                executed((1, 1, 0, 1, 1, 1), (0, 1, 0, 1, 1, 1), (0, 0, 0, 1, 0, 1)))
            for sample, suffix, opened, closed in (
                ('daily', 'D', ('2024-02-29', '2024-03-01'), ('2024-03-01', '2024-03-02')),
                ('weekly', 'W', ('2024-02-26', '2024-03-04'), ('2024-03-04', '2024-03-11')),
                ('monthly', 'M', ('2024-02-01', '2024-03-01'), ('2024-03-01', '2024-04-01')),
            ):
                spelling = '1' + suffix if version == 6 else suffix
                add(version, f'{sample}-chart', f'plot(timeframe.period == "{spelling}" ? 1 : 0)\n'
                    'plot(time(timeframe.period))\nplot(time_close(""))\nplot(timeframe.change() ? 1 : 0)',
                    executed((1, 1), date_rows(*opened), date_rows(*closed), (1, 1),
                             qualifiers=['simple', 'series', 'series', 'series']), sample)

    add(6, 'weekly-to-monthly', 'plot(time("1M"))\nplot(time_close("1M"))',
        executed(date_rows('2024-02-01', '2024-03-01'), date_rows('2024-03-01', '2024-04-01')), 'weekly')
    for name, call, code, sample in (
        ('lower', 'time("60")', 'SESSION_TIMEFRAME', 'two-hour'),
        ('not-divisible', 'time("180")', 'SESSION_TIMEFRAME', 'two-hour'),
        ('day-not-divisible', 'time("D")', 'SESSION_TIMEFRAME', 'seven-minute'),
        ('multiday', 'time("2D")', 'SESSION_TIMEFRAME', 'hourly'),
        ('htf-session', 'time("1D", "0900-1600")', 'SESSION_TIMEFRAME', 'hourly'),
        ('htf-zone', 'time_close("1D", "24x7", "America/New_York")', 'SESSION_ZONE', 'hourly'),
    ):
        add(6, name, f'plot({call})', dict(status='rejected', category='RUNTIME_ERROR', code=code), sample)

    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='UTC minute multiples, Monday boundaries, February 2024 and previous physical observations.'))) + b'\n'
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
    print('Higher timeframe fixtures verified.')


if __name__ == '__main__':
    main()
