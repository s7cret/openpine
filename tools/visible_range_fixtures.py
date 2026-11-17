"""Independent visible-candle range expectations over sparse OHLCV history."""

import argparse

from packages.contracts.wire import canonical
from tools.higher_timeframes_fixtures import bars, stamp
from tools.specification import ROOT

DIRECTORY = 'conformance/visible-range'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/visible-range/high-low.pine'
TITLE = 'Visible high and low'
OPENINGS = tuple(map(stamp, ('2024-02-29T12:00', '2024-02-29T12:01', '2024-02-29T12:03',
                             '2024-02-29T12:04', '2024-02-29T12:05')))
LEFT = 'chart.left_visible_bar_time'
RIGHT = 'chart.right_visible_bar_time'
FIRST = dict(left_ms=OPENINGS[1], right_ms=OPENINGS[2])
SECOND = dict(left_ms=OPENINGS[2], right_ms=OPENINGS[3])
SINGLE = dict(left_ms=OPENINGS[2], right_ms=OPENINGS[2])
FULL = dict(left_ms=OPENINGS[0], right_ms=OPENINGS[-1])
GAP = dict(left_ms=OPENINGS[1] + 60_000, right_ms=OPENINGS[3])
EXTREMES = (
    (FULL, (11., 15., 15., 17., 17.), (8., 7., 7., 5., 5.)),
    (FIRST, (None, 15., 15., 15., 15.), (None, 7., 7., 7., 7.)),
    (SECOND, (None, None, 12., 17., 17.), (None, None, 9., 5., 5.)),
    (SINGLE, (None, None, 12., 12., 12.), (None, None, 9., 9., 9.)),
)
BODY = '''scale = input.int(1, "Scale")
var float visibleHigh = na
var float visibleLow = na
if time >= chart.left_visible_bar_time and time <= chart.right_visible_bar_time
    visibleHigh := na(visibleHigh) ? high : math.max(visibleHigh, high)
    visibleLow := na(visibleLow) ? low : math.min(visibleLow, low)
plot(visibleHigh * scale, "Visible high")
plot(visibleLow * scale, "Visible low")
plot(chart.left_visible_bar_time, "Left")
plot(chart.right_visible_bar_time, "Right")'''


def source(version, body=BODY):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}")\n{body}\n'


def values(bounds, scale=1):
    _, high, low = next(row for row in EXTREMES if row[0] == bounds)
    return (*(tuple(value * scale if value is not None else None for value in row) for row in (high, low)),
            (bounds['left_ms'],) * len(OPENINGS), (bounds['right_ms'],) * len(OPENINGS))


def fixtures():
    data = bars('1', OPENINGS)
    data.update(context_id='visible-minute', symbol='TEST:VISIBLE')
    for row, high, low in zip(data['bars'], (11., 15., 12., 17., 16.), (8., 7., 9., 5., 6.), strict=True):
        row.update(high=high, low=low)
    files = {DIRECTORY + '/bars.json': canonical(data) + b'\n', EXAMPLE: source(6).encode()}
    cases = []
    for version in range(1, 7):
        if version < 5:
            for name, variable in (('left', LEFT), ('right', RIGHT)):
                filename = f'v{version}-{name}.pine'
                files[f'{DIRECTORY}/{filename}'] = source(version, f'plot({variable})').encode()
                cases.append(dict(case_id=f'v{version}-{name}-unavailable', language_version=version, source=filename,
                                  bars='bars.json', expected=dict(status='rejected', category='PROFILE_UNCOVERED',
                                                                 code='ENVIRONMENT_VERSION')))
            continue
        filename = f'v{version}-extremes.pine'
        files[f'{DIRECTORY}/{filename}'] = source(version).encode()
        base = dict(language_version=version, source=filename, bars='bars.json')
        for name, bounds in zip(('full', 'first', 'second', 'single'), (FULL, FIRST, SECOND, SINGLE), strict=True):
            outputs = [dict(title=title, type=kind, values=row) for title, kind, row in zip(
                ('Visible high', 'Visible low', 'Left', 'Right'), ('float', 'float', 'int', 'int'), values(bounds), strict=True)]
            case = dict(base, case_id=f'v{version}-{name}', expected=dict(status='executed', title=TITLE,
                shorttitle='', overlay=False, observation_count=len(OPENINGS), qualifiers=['series', 'series', 'input', 'input'],
                outputs=outputs))
            if name != 'full':case['visible_range'] = bounds
            cases.append(case)
        cases.append(dict(base, case_id=f'v{version}-missing-candle', visible_range=GAP,
                          expected=dict(status='rejected', category='INPUT_INVALID', code='VISIBLE_RANGE_CANDLE')))
    files[CASES] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Exact opening times of the inclusive visible range over a sparse snapshot.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    for name, content in fixtures().items():
        path = ROOT / name
        if check:
            assert path.read_bytes() == content, name
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print('Visible-range fixtures verified.')


if __name__ == '__main__':
    main()
