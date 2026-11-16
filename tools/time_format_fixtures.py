"""Hand-checked clock strings, DST boundaries and a replay consumer."""

import argparse
import json
from datetime import UTC, datetime, timedelta

from packages.contracts.calendar import UTC_EPOCH
from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT
from tools.tick_price_fixtures import bars


DIRECTORY = 'conformance/time-format'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/time-format/dst-alert.pine'
TITLE = 'Time format'
PRIMARY = MarketContext('time-format-primary', 'TEST:CLOCK', '1', 'recorded.1')
ENVIRONMENTS = None
MASK = "yyyy-MM-dd HH:mm Z"
NY_ZONE = 'America/New_York'
SPRING_BEFORE = '2025-03-09 01:59 -0500'
SPRING_AFTER = '2025-03-09 03:00 -0400'
FALL_FIRST = '2025-11-02 01:30 -0400'
FALL_SECOND = '2025-11-02 01:30 -0500'
PREVIEW = 'Открытие 2025-11-02 01:59 -0400 | закрытие 2025-11-02 01:00 -0500'
ALERT = (2, PREVIEW)


def milliseconds(*args):
    return (datetime(*args, tzinfo=UTC) - UTC_EPOCH) // timedelta(milliseconds=1)


def tape():
    start = milliseconds(2025, 11, 2, 5, 59)
    rows = (('open', 0, 0, 0), ('update', 0, 30_000, 1), ('close', 0, 60_000, 2),
            ('open', 60_000, 60_000, 0), ('close', 60_000, 120_000, 1))
    return tuple(MarketEvent(seq, PRIMARY.context_id, kind, start + opening, start + at,
                             revision, 10., 10., 10., 10., 1.)
                 for seq, (kind, opening, at, revision) in enumerate(rows))


def example_input():
    candles = ((10., 10., 10., 10.),) * 2
    result = bars(PRIMARY, candles)
    for row, event in zip(result['bars'], (e for e in tape() if e.kind == 'close'), strict=True):
        row['time'] = event.time
    return result


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path, bars='bars.json', expected=expected))

    def plot(call, expected):
        return f'plot({call} == {json.dumps(expected, ensure_ascii=False)} ? 1.0 : 0.0)'

    def executed(*values):
        outputs = [dict(title='', type='float', values=value) for value in values]
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=2, qualifiers=['series'] * len(outputs), outputs=outputs)

    def rejected(code, category='RUNTIME_ERROR'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 5):
        add(version, 'unavailable', plot('str.format_time(0)', ''), rejected('SIGNATURE_UNAVAILABLE', 'LANGUAGE_INVALID'))

    yes = (1., 1.)
    for version in (5, 6):
        add(version, 'default', '\n'.join((
            plot('str.format_time(time=0)', '1970-01-01T00:00:00+0000'),
            plot('str.format_time(time_close, "HH:mm")', '06:00'),
        )), executed(yes, (1., 0.)))
        add(version, 'fixed-offsets', '\n'.join((
            plot('str.format_time(0, timezone="UTC-5")', '1969-12-31T19:00:00-0500'),
            plot('str.format_time(0, timezone="UTC+05:30")', '1970-01-01T05:30:00+0530'),
            plot('str.format_time(0, timezone="GMT+0100")', '1970-01-01T01:00:00+0100'),
        )), executed(yes, yes, yes))
        add(version, 'na-and-milliseconds', '\n'.join((
            plot('str.format_time(na, timezone="America/New_York")', '1969-12-31T19:00:00-0500'),
            plot('str.format_time(-1, "yyyyMMdd\'T\'HHmmss.SSSZ", "UTC")', '19691231T235959.999+0000'),
            plot('str.format_time(7, "ss.SSS \'текст\' \'\'", "UTC")', "00.007 текст '"),
        )), executed(yes, yes, yes))
        boundaries = (
            (milliseconds(2025, 3, 9, 6, 59), SPRING_BEFORE),
            (milliseconds(2025, 3, 9, 7), SPRING_AFTER),
            (milliseconds(2025, 11, 2, 5, 30), FALL_FIRST),
            (milliseconds(2025, 11, 2, 6, 30), FALL_SECOND),
        )
        add(version, 'dst', '\n'.join(plot(f'str.format_time({instant}, "{MASK}", "{NY_ZONE}")', expected)
                                     for instant, expected in boundaries), executed(yes, yes, yes, yes))
        add(version, 'series-pattern-and-zone', '\n'.join((
            'mask = bar_index == 0 ? "HH:mm Z" : "HH:mm:ss Z"',
            'zone = bar_index == 0 ? "UTC-5" : "Asia/Kolkata"',
            plot('str.format_time(0, mask, zone)', '19:00 -0500'),
            plot('str.format_time(timezone=zone, format=mask, time=0)', '05:30:00 +0530'),
        )), executed((1., 0.), (0., 1.)))
        for name, call, code in (
            ('invalid-offset', 'str.format_time(0, timezone="UTC+05:60")', 'TIME_FORMAT_ZONE'),
            ('unknown-zone', 'str.format_time(0, timezone="america/new_york")', 'TIME_FORMAT_ZONE'),
            ('invalid-zone', 'str.format_time(0, timezone="Asia/../UTC")', 'TIME_FORMAT_ZONE'),
            ('out-of-range', 'str.format_time(253402300800000)', 'TIME_FORMAT_RANGE'),
            ('unclosed-quote', 'str.format_time(0, "\'HH")', 'TIME_FORMAT_PATTERN'),
            ('unsupported-token', 'str.format_time(0, "MMM")', 'TIME_FORMAT_PATTERN'),
        ):
            add(version, name, plot(call, ''), rejected(code))
        add(version, 'invalid-type', plot('str.format_time(1.5)', ''), rejected('ARGUMENT_TYPE', 'LANGUAGE_INVALID'))

    files['bars.json'] = canonical(example_input()) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Documented epoch and mask rules, fixed UTC offsets and New York DST transitions.'))) + b'\n'
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
    print('Time format fixtures verified.')


if __name__ == '__main__':
    main()
