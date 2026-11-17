"""Independent historical and replay expectations for the Pine execution clock."""

import argparse

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.higher_timeframes_fixtures import bars, stamp
from tools.specification import ROOT


DIRECTORY = 'conformance/execution-time'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/execution-time/update-gaps.pine'
TITLE = 'Execution clock'
SYMBOL = 'TEST:EXECUTION'
CONTEXT_ID = 'execution-minute'
OPENINGS = tuple(map(stamp, ('2024-02-29T12:00', '2024-02-29T12:01', '2024-02-29T12:02')))
EXECUTION_TIME = stamp('2024-02-29T12:03') + 789
CONFIRMED_TIMES = (OPENINGS[0] + 63_000, OPENINGS[1] + 63_000, OPENINGS[2] + 68_000)
HISTORICAL_OUTPUTS = ((EXECUTION_TIME,) * len(OPENINGS), (0,) * len(OPENINGS))
REPLAY_OUTPUTS = (CONFIRMED_TIMES, (0, 49_000, 45_000))
OPEN_CUT = 2
UPDATE_CUT = 3
EXAMPLE_BODY = '''varip int previous = na
varip int gap = 0
if barstate.isnew or na(previous)
    gap := 0
else
    gap := timenow - previous
previous := timenow
plot(timenow, "Clock")
plot(gap, "Update gap")
alertcondition(barstate.isconfirmed, "Execution time", '{{timenow}}|{{plot("Clock")}}')'''


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def tape():
    rows = []
    for index, (opening, closed) in enumerate(zip(OPENINGS, CONFIRMED_TIMES, strict=True)):
        if index:
            rows.append(MarketEvent(len(rows), CONTEXT_ID, 'open', opening, opening + 4_000,
                                    0, 10., 10., 10., 10., 0.))
            update_ms = 14_000 if index == 1 else 23_000
            rows.append(MarketEvent(len(rows), CONTEXT_ID, 'update', opening, opening + update_ms,
                                    1, 10., 11., 9., 10., 5.))
        rows.append(MarketEvent(len(rows), CONTEXT_ID, 'close', opening, closed,
                                2 if index else 0, 10., 11., 9., 10., 10.))
    return tuple(rows)


def fixtures():
    keys = {'schema_version': 'openpine.conformance.cases.1',
            'implementation_profile_id': 'openpine.historical-bars.1', 'rule_refs': [], 'assertion_refs': [],
            'oracle': {'kind': 'independent_manual_expectations', 'external_pine_execution': False,
                       'basis': 'A single explicitly frozen Unix millisecond per historical run.'}}
    data = bars('1', OPENINGS)
    data.update(context_id=CONTEXT_ID, symbol=SYMBOL)
    files = {DIRECTORY + '/bars.json': canonical(data) + b'\n', EXAMPLE: source(6, EXAMPLE_BODY).encode()}
    cases = []
    for version in range(1, 7):
        filename = f'v{version}-clock.pine'
        files[f'{DIRECTORY}/{filename}'] = source(version, 'plot(timenow, "Clock")\nplot(time, "Open")').encode()
        base = dict(language_version=version, source=filename, bars='bars.json')
        if version < 3:
            cases.append(dict(base, case_id=f'v{version}-unavailable', execution_time_ms=EXECUTION_TIME,
                expected=dict(status='rejected', category='PROFILE_UNCOVERED', code='ENVIRONMENT_VERSION')))
            continue
        cases.append(dict(base, case_id=f'v{version}-frozen', execution_time_ms=EXECUTION_TIME,
            expected=dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                          observation_count=len(OPENINGS), qualifiers=['series', 'series'],
                          outputs=[dict(title='Clock', type='int', values=HISTORICAL_OUTPUTS[0]),
                                   dict(title='Open', type='int', values=OPENINGS)])))
        cases.append(dict(base, case_id=f'v{version}-missing-clock',
            expected=dict(status='rejected', category='INPUT_UNAVAILABLE', code='EXECUTION_TIME_REQUIRED')))
    files[CASES] = canonical(dict(keys, cases=cases)) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    for name, content in fixtures().items():
        path = ROOT / name
        if check:
            assert path.read_bytes() == content, name
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print('Execution time fixtures verified.')


if __name__ == '__main__':
    main()
