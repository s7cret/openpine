"""Date compilation, input types and a strategy with a half-open entry window."""

import argparse
import json

from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/date-inputs'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/date-inputs/date-window.pine'
TITLE = 'Date input'
MIDNIGHT = 1762041600000
MINUTE = 60_000
DEFAULT_WINDOW = (MIDNIGHT + MINUTE, MIDNIGHT + 3 * MINUTE)
OVERRIDE_WINDOW = (MIDNIGHT + 2 * MINUTE, MIDNIGHT + 5 * MINUTE)
DEFAULT_ORDERS = (1, 2)
OVERRIDE_ORDERS = (2, 3, 4)


def bars():
    return dict(schema_version='openpine.historical-bars.2', context_id='date-primary',
                symbol='TEST:DATE', timeframe='1',
                bars=[dict(time=MIDNIGHT + i * MINUTE, open=10. + i, high=11. + i,
                           low=9. + i, close=10. + i, volume=10.) for i in range(6)])


def inputs(window):
    return dict(zip(('start', 'end'), window, strict=True))


def time_input(version, default, metadata=''):
    if version == 4:
        return f'input({default}, type=input.time{metadata})'
    return f'input.time({default}{metadata})'


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

    def executed(qualifier, *values):
        outputs = [dict(title='', type='float', values=value) for value in values]
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=6, qualifiers=[qualifier] * len(values), outputs=outputs)

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    def compare(call, value):
        return f'plot({call} == {json.dumps(value)} ? 1.0 : 0.0)'

    for version in (1, 2, 3):
        diagnostic = rejected('ENVIRONMENT_VERSION', 'PROFILE_UNCOVERED') if version == 1 else rejected('SIGNATURE_UNAVAILABLE')
        add(version, 'timestamp-unavailable', 'plot(timestamp("2025-11-02"))', diagnostic)
        add(version, 'input-unavailable', 'start=input(0, type=input.time)\nplot(start)',
            rejected('INPUT_DECLARATION', 'PROFILE_UNCOVERED'))

    yes = (1.,) * 6
    for version in (4, 5, 6):
        add(version, 'absolute-date', '\n'.join((
            compare('timestamp("2025-11-02")', 1762041600000),
            compare('timestamp("02 Nov 2025 01:30 -0400")', 1762061400000),
            compare('timestamp("2024-02-29T23:59:59.001+05:30")', 1709231399001),
            compare('timestamp(dateString="1970-01-01T00:00:00.001+01:00")', -3599999),
        )), executed('const', yes, yes, yes, yes))
        start = time_input(version, 'startDefault', ', title="Start"')
        end = time_input(version, 'timestamp("02 Nov 2025 00:03 +0000")')
        add(version, 'date-input', '\n'.join((
            'day="2025-11-"+"02"', 'startDefault=timestamp(day)+60000',
            f'start={start}', f'end={end}', 'plot(time >= start and time < end ? 1.0 : 0.0)',
        )), executed('series', (0., 1., 1., 0., 0., 0.)))
        add(version, 'bad-date', 'plot(timestamp("2025-02-29"))', rejected('TIMESTAMP_DATE'))
        add(version, 'bad-offset', 'plot(timestamp("2025-11-02T00:00+05:60"))', rejected('TIMESTAMP_DATE'))
        add(version, 'dynamic-date', 'day=time > 0 ? "2025-11-02" : "2025-11-03"\nplot(timestamp(day))',
            rejected('TIMESTAMP_CONST_DATE', 'PROFILE_UNCOVERED'))
        add(version, 'nonconstant-default', 'start=' + time_input(version, 'time') + '\nplot(start)',
            rejected('INPUT_DECLARATION', 'PROFILE_UNCOVERED'))
    files['bars.json'] = canonical(bars()) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='UTC Unix milliseconds and known absolute dates, with half-open input window boundaries.'))) + b'\n'
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
    print('Date input fixtures verified.')


if __name__ == '__main__':
    main()
