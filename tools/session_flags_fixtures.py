"""Independent session flag expectations for continuous UTC markets."""

import argparse

from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.higher_timeframes_fixtures import bars, stamp
from tools.specification import ROOT


DIRECTORY = 'conformance/session-flags'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/session-flags/daily-volume.pine'
TITLE = 'UTC session volume'
SYMBOL = 'TEST:SESSION'
CONTEXT_ID = 'session-minute'
OPENINGS = tuple(map(stamp, ('2024-02-28T12:00', '2024-02-28T23:59', '2024-02-29T00:04',
                             '2024-02-29T20:00', '2024-03-01T00:02', '2024-03-01T23:59')))
VOLUMES = (2., 3., 7., 11., 13., 17.)
OUTPUTS = (
    ('First', 'int', (1, 0, 1, 0, 1, 0)),
    ('Last', 'int', (0, 1, 0, 0, 0, 1)),
    ('First regular', 'int', (1, 0, 1, 0, 1, 0)),
    ('Last regular', 'int', (0, 1, 0, 0, 0, 1)),
    ('Session volume', 'float', (None, 5., None, None, None, 30.)),
)
VOLUME_BODY = '''var float dailyVolume = 0.0
if session.isfirstbar
    dailyVolume := volume
else
    dailyVolume := dailyVolume + volume
plot(session.isfirstbar ? 1 : 0, "First")
plot(session.islastbar ? 1 : 0, "Last")
plot(session.isfirstbar_regular ? 1 : 0, "First regular")
plot(session.islastbar_regular ? 1 : 0, "Last regular")
plot(session.islastbar and barstate.isconfirmed ? dailyVolume : na, "Session volume")'''
MARKET = ('session.ismarket', 'session.ispremarket', 'session.ispostmarket')
BOUNDARIES = ('session.isfirstbar', 'session.islastbar', 'session.isfirstbar_regular', 'session.islastbar_regular')
PAUSE_AFTER = 3
OPEN_CUT = 2
UPDATE_CUT = 3


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def tape():
    rows = []
    for index, (opening, volume) in enumerate(zip(OPENINGS, VOLUMES, strict=True)):
        revision = 0
        if index in (1, 2):
            rows.append(MarketEvent(len(rows), CONTEXT_ID, 'open', opening, opening,
                                    0, 10., 10., 10., 10., 0.))
            rows.append(MarketEvent(len(rows), CONTEXT_ID, 'update', opening, opening + 30_000,
                                    1, 10., 11., 9., 10., 1.))
            revision = 2
        rows.append(MarketEvent(len(rows), CONTEXT_ID, 'close', opening, opening + 60_000,
                                revision, 10., 11., 9., 10., volume))
    return tuple(rows)


def markers(names):
    return '\n'.join(f'plot({name} ? 1 : 0, "{name}")' for name in names)


def fixtures():
    datasets = {
        'sparse': ('1', OPENINGS),
        'daily': ('D', tuple(map(stamp, ('2024-02-28', '2024-02-29', '2024-03-01')))),
        'weekly': ('W', tuple(map(stamp, ('2024-02-26', '2024-03-04')))),
        'monthly': ('M', tuple(map(stamp, ('2024-02-01', '2024-03-01')))),
        'seven-minute': ('7', (0, 7 * 60_000)),
    }
    files = {}
    for name, (period, opened) in datasets.items():
        dataset = bars(period, opened)
        dataset.update(context_id=CONTEXT_ID if name == 'sparse' else 'session-' + name, symbol=SYMBOL)
        if name == 'sparse':
            for row, volume in zip(dataset['bars'], VOLUMES, strict=True):
                row['volume'] = volume
        files[name + '.json'] = canonical(dataset) + b'\n'
    cases = []

    def add(version, name, body, *, dataset='sparse', outputs=(), reject=None):
        case_id = f'v{version}-{name}'
        filename = case_id + '.pine'
        files[filename] = source(version, body).encode()
        if reject:
            expected = dict(status='rejected', category='PROFILE_UNCOVERED', code=reject)
        else:
            expected = dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                            observation_count=len(datasets[dataset][1]), qualifiers=['series'] * len(outputs),
                            outputs=[dict(title=title, type=kind, values=values) for title, kind, values in outputs])
        cases.append(dict(case_id=case_id, language_version=version, source=filename,
                          bars=dataset + '.json', expected=expected))

    for version in range(1, 7):
        unavailable = (*MARKET, *BOUNDARIES) if version < 4 else BOUNDARIES if version == 4 else ()
        for flag in unavailable:
            add(version, flag.removeprefix('session.') + '-unavailable', markers((flag,)),
                reject='ENVIRONMENT_VERSION')
        if version >= 4:
            for name, (_, opened) in datasets.items():
                outputs = tuple((flag, 'int', (value,) * len(opened))
                                for flag, value in zip(MARKET, (1, 0, 0), strict=True))
                add(version, name + '-market', markers(MARKET), dataset=name, outputs=outputs)
        if version >= 5:
            add(version, 'daily-volume', VOLUME_BODY, outputs=OUTPUTS)
            add(version, 'daily-boundaries', markers(BOUNDARIES), dataset='daily',
                outputs=tuple((flag, 'int', (1, 1, 1)) for flag in BOUNDARIES))
            for name, flag in zip(('weekly', 'monthly', 'seven-minute'), BOUNDARIES[:3], strict=True):
                add(version, name + '-boundaries', markers((flag,)), dataset=name, reject='SESSION_FLAG_TIMEFRAME')

    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='UTC day boundaries of received candles and fixed market states in the continuous profile.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    files = {f'{DIRECTORY}/{name}': content for name, content in fixtures().items()}
    files[EXAMPLE] = source(6, VOLUME_BODY).encode()
    for name, content in files.items():
        path = ROOT / name
        if check:
            assert path.read_bytes() == content, name
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(content)
    print('Session flag fixtures verified.')


if __name__ == '__main__':
    main()
