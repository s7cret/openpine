"""Independent expectations for versioned calendar parts and wall times."""

import argparse

from packages.contracts.wire import canonical
from tools.higher_timeframes_fixtures import bars, stamp
from tools.specification import ROOT


DIRECTORY = 'conformance/calendar-zones'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/calendar-zones/local-boundary.pine'
TITLE = 'Calendar zones'
PAUSE_AFTER = 2
FOLD = ('2024-11-03T00:30', '2024-11-03T05:30', '2024-11-03T06:30', '2024-11-03T08:00')
BOUNDARY = 1730620800000
EXAMPLE_OUTPUTS = ((0, 5, 6, 8), (20, 1, 1, 3), (2, 3, 3, 3),
                   (None, 20, 1, 1), (0, 0, 0, 1), (BOUNDARY,) * 4)


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def fixtures():
    batches = {
        'week': ('2015-12-31T23:00', '2016-01-01', '2016-01-03T23:30', '2016-01-04T00:30', '2016-01-04T06:00'),
        'fall': FOLD,
        'spring': ('2024-03-10T06:59', '2024-03-10T07:00'),
    }
    files = {name + '.json': canonical(bars('1', map(stamp, opened))) + b'\n'
             for name, opened in batches.items()}
    cases = []

    def add(version, name, body, *, dataset='week', values=(), qualifiers=None, error=None):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        if error is None:
            expected = dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                observation_count=len(batches[dataset]), qualifiers=qualifiers or ['series'] * len(values),
                outputs=[dict(title='', type='int', values=row) for row in values])
        else:
            category, code = error
            expected = dict(status='rejected', category=category, code=code)
        cases.append(dict(case_id=identifier, language_version=version, source=path,
                          bars=dataset + '.json', expected=expected))

    def repeated(value, dataset='week'):
        return (value,) * len(batches[dataset])

    year_weeks = (53, 53, 53, 1, 1)
    for version in range(1, 7):
        add(version, 'week', 'plot(weekofyear)\nplot(weekofyear(time))', values=(year_weeks, year_weeks))
        if version < 4:
            add(version, 'part-zone-unavailable', 'plot(hour(time, "UTC"))',
                error=('LANGUAGE_INVALID', 'CALENDAR_ZONE_VERSION'))
        else:
            add(version, 'zoned-parts',
                'plot(year(time,"America/New_York"))\nplot(month(time,"America/New_York"))\n'
                'plot(weekofyear(time,"America/New_York"))\nplot(dayofmonth(time,"America/New_York"))\n'
                'plot(dayofweek(time,"America/New_York"))\nplot(hour(time,"America/New_York"))\n'
                'plot(minute(time,"UTC+05:45"))\nplot(second(time+7,timezone="UTC"))',
                values=((2015, 2015, 2016, 2016, 2016), (12, 12, 1, 1, 1), (53, 53, 53, 53, 1),
                        (31, 31, 3, 3, 4), (5, 5, 1, 1, 2), (18, 19, 18, 19, 1),
                        (45, 45, 15, 15, 45), repeated(0)))
        if version in (3, 4, 5, 6):
            hours = ',0,0' if version == 3 else ''
            add(version, 'numeric-zones',
                f'plot(timestamp("UTC+05:30",2024,1,1{hours}))\n'
                'plot(timestamp("UTC",year=year,month=1,day=1,hour=0,minute=0,second=1))',
                values=(repeated(1704047400000), (1420070401000, *repeated(1451606401000)[:4])),
                qualifiers=['simple', 'series'])

    add(1, 'timestamp-unavailable', 'plot(timestamp("UTC",2024,1,1,0,0))',
        error=('PROFILE_UNCOVERED', 'ENVIRONMENT_VERSION'))
    add(2, 'timestamp-zone-unavailable', 'plot(timestamp("UTC",2024,1,1,0,0))',
        error=('LANGUAGE_INVALID', 'CALENDAR_ZONE_VERSION'))
    add(6, 'fold-to-parts', 'zone = time < 1730615400000 ? "America/New_York" : "US/Eastern"\n'
        'plot(hour(time,zone))\nplot(minute(time,zone))\n'
        'plot(timestamp("UTC-4",2024,11,3,1,30))\nplot(timestamp("UTC-5",2024,11,3,1,30))', dataset='fall',
        values=((20, 1, 1, 3), (30, 30, 30, 0), repeated(1730611800000, 'fall'), repeated(1730615400000, 'fall')),
        qualifiers=['series', 'series', 'simple', 'simple'])
    add(6, 'spring-to-parts', 'plot(hour(time,"America/New_York"))\nplot(minute(time,"America/New_York"))',
        dataset='spring', values=((1, 3), (59, 0)))
    add(6, 'before-epoch', 'plot(year(-1,"UTC"))\nplot(weekofyear(-1,"UTC"))\n'
        'plot(timestamp("UTC+01",1970,1,1))',
        values=(repeated(1969), repeated(1), repeated(-3600000)), qualifiers=['series', 'series', 'simple'])
    add(6, 'na-propagation', 'plot(hour(int(na),"UTC"))\nplot(timestamp("UTC",int(na),1,1))',
        values=(repeated(None), repeated(None)), qualifiers=['series', 'simple'])
    for name, expression, code in (
        ('invalid-zone', 'hour(time,"America/Unknown")', 'CALENDAR_ZONE'),
        ('overflow', 'year(253402300800000,"UTC")', 'CALENDAR_RANGE'),
        ('fold', 'timestamp("America/New_York",2024,11,3,1,30)', 'TIMESTAMP_AMBIGUOUS'),
        ('gap', 'timestamp("America/New_York",2024,3,10,2,30)', 'TIMESTAMP_NONEXISTENT'),
        ('invalid-date', 'timestamp("America/New_York",2023,2,29)', 'ENVIRONMENT_DATE'),
    ):
        add(6, name, f'plot({expression})', error=('RUNTIME_ERROR', code))
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='ISO week boundaries, Unix dates, fixed offsets and New York DST changes in 2024.'))) + b'\n'
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
    print('Calendar zone fixtures verified.')


if __name__ == '__main__':
    main()
