"""Independent points around trading hours, gaps and UTC/DST boundaries."""

import argparse
from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/trading-sessions'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/trading-sessions/entry-window.pine'
TITLE = 'Trading session'
HOUR = 3_600_000
SUNDAY = 1762041600000
MONDAY = SUNDAY + 24 * HOUR
SPRING = 1741478400000
DEFAULT = '0000-0003'
OVERRIDE = '0002-0006'
DATE_RANGE = (SUNDAY + 60_000, SUNDAY + 300_000)
DEFAULT_ORDERS = (1, 2)
OVERRIDE_ORDERS = (2, 3, 4)


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def session_input(version, default):
    if version < 5:
        return f'input("{default}", type={"session" if version <= 3 else "input.session"})'
    return f'input.session("{default}")'


def bars(start, count, minutes):
    return dict(schema_version='openpine.historical-bars.2', context_id='session-primary',
                symbol='TEST:SESSION', timeframe=str(minutes),
                bars=[dict(time=start + i * minutes * 60_000, open=10. + i, high=11. + i,
                           low=9. + i, close=10. + i, volume=10.) for i in range(count)])


def fixtures():
    datasets = {
        'weekday': bars(MONDAY + 8 * HOUR, 5, 60),
        'weekend': bars(SUNDAY + 8 * HOUR, 5, 60),
        'overnight': bars(SUNDAY + 21 * HOUR, 7, 60),
        'spring': bars(SPRING + 6 * HOUR, 4, 60),
        'fall': bars(SUNDAY + 4 * HOUR, 5, 60),
        'strategy': bars(SUNDAY, 6, 1),
    }
    files = {key + '.json': canonical(value) + b'\n' for key, value in datasets.items()}
    cases = []

    def add(version, name, body, expected, sample='weekday'):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path,
                          bars=sample + '.json', expected=expected))

    def executed(*values):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(values[0]), qualifiers=['series'] * len(values),
                    outputs=[dict(title='', type='int', values=row) for row in values])

    def rejected(code, category='PROFILE_UNCOVERED'):
        return dict(status='rejected', category=category, code=code)

    def offsets(origin, *hours):
        return tuple(None if hour is None else origin + int(hour * HOUR) for hour in hours)

    for version in range(1, 7):
        tf = 'period' if version < 4 else 'timeframe.period'
        config = f'window={session_input(version, "0900-1130")}\n' if version >= 3 else ''
        argument = 'window' if config else '"0900-1130"'
        add(version, 'weekday', f'{config}plot(time({tf}, {argument}))',
            executed(offsets(MONDAY, None, 9, 10, 11, None)))
        add(version, 'implicit-session', f'plot(time({tf}))', executed(offsets(MONDAY, 8, 9, 10, 11, 12)))
        add(version, 'weekend-default', f'plot(time({tf}, "0900-1130"))',
            executed(offsets(SUNDAY, None, 9, 10, 11, None) if version >= 5 else (None,) * 5), 'weekend')
        if version < 3:
            add(version, 'input-unavailable', f'window={session_input(version, "0900-1130")}\nplot(1)',
                rejected('INPUT_DECLARATION'))
        if version < 4:
            add(version, 'close-unavailable', f'plot(time_close({tf}))',
                rejected('SIGNATURE_UNAVAILABLE', 'LANGUAGE_INVALID'))
        if version >= 4:
            parameter = 'resolution' if version == 4 else 'timeframe'
            add(version, 'clipped-close', f'plot(time_close({parameter}={tf}, session="0900-1130"))\nplot(time_close)',
                executed(offsets(MONDAY, None, 10, 11, 11.5, None), offsets(MONDAY, 9, 10, 11, 12, 13)))

    add(6, 'overnight', 'plot(time("", "2200-0200:2"))\nplot(time_close("", "2200-0200:2"))',
        executed(offsets(SUNDAY, None, 22, 23, 24, 25, None, None),
                 offsets(SUNDAY, None, 23, 24, 25, 26, None, None)), 'overnight')
    add(6, 'overnight-midnight-end', 'plot(time("", "2200-0000:2"))',
        executed(offsets(SUNDAY, None, 22, 23, None, None, None, None)), 'overnight')
    add(6, 'split', 'plot(time("", "0800-1000,1100-1200:2"))',
        executed(offsets(MONDAY, 8, 9, None, 11, None)))
    call = 'plot(time_close("", "0100-0400:1", "America/New_York"))'
    add(6, 'spring-forward', call, executed(offsets(SPRING, 7, 8, None, None)), 'spring')
    call = 'plot(time_close("", "0000-0300:1", "America/New_York"))'
    add(6, 'fall-back', call, executed(offsets(SUNDAY, 5, 6, 7, 8, None)), 'fall')
    add(6, 'fixed-offset', 'plot(time("", "1000-1200:2", "UTC+01:00"))',
        executed(offsets(MONDAY, None, 9, 10, None, None)))
    add(6, 'explicit-all-week', 'plot(time("", "24x7"))',
        executed(offsets(SUNDAY, 8, 9, 10, 11, 12)), 'weekend')
    for name, call, code in (
        ('bad-clock', 'time("", "0960-1130")', 'SESSION_STRING'),
        ('overlap', 'time("", "0900-1100,1000-1130")', 'SESSION_STRING'),
        ('misaligned', 'time_close("", "0930-1130")', 'SESSION_ALIGNMENT'),
        ('different-timeframe', 'time("120", "0900-1130")', 'SESSION_TIMEFRAME'),
        ('bad-zone', 'time("", "0900-1130", "Everywhere/Nowhere")', 'SESSION_ZONE'),
        ('missing-dst-boundary', 'time_close("", "0100-0230:1", "America/New_York")', 'SESSION_DST_BOUNDARY'),
        ('ambiguous-dst-boundary', 'time_close("", "0030-0130:1", "America/New_York")', 'SESSION_DST_BOUNDARY'),
    ):
        add(6, name, f'plot({call})', rejected(code, 'RUNTIME_ERROR'),
            {'missing-dst-boundary': 'spring', 'ambiguous-dst-boundary': 'fall'}.get(name, 'weekday'))
    for version, name, call, code, category in (
        (1, 'legacy-named', 'time(x=period)', 'NAMED_ARGUMENT_UNVERIFIED', 'PROFILE_UNCOVERED'),
        (3, 'zone-unavailable', 'time(resolution=period, timezone="UTC")', 'UNKNOWN_ARGUMENT', 'LANGUAGE_INVALID'),
        (5, 'bars-back', 'time("", bars_back=1)', 'UNKNOWN_ARGUMENT', 'LANGUAGE_INVALID'),
        (6, 'timeframe-bars-back', 'time("", timeframe_bars_back=1)', 'UNKNOWN_ARGUMENT', 'LANGUAGE_INVALID'),
        (6, 'dynamic-session', 'time("", time > 0 ? "0900-1100" : "1200-1400")',
         'ARGUMENT_QUALIFIER', 'LANGUAGE_INVALID'),
    ):
        add(version, name, f'plot({call})', rejected(code, category))
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-calculated wall-clock membership, UTC boundaries and DST in America/New_York.'))) + b'\n'
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
    print('Trading session fixtures verified.')


if __name__ == '__main__':
    main()
