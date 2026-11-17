"""Expected chart boundaries and UTC trading dates in Pine v1 through v6."""

import argparse

from packages.contracts.wire import canonical
from tools.higher_timeframes_fixtures import bars, stamp
from tools.specification import ROOT


DIRECTORY = 'conformance/chart-clock'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/chart-clock/recent-bars.pine'
TITLE = 'Chart clock'
SYMBOL = 'TEST:CLOCK'
SPARSE_ID = 'clock-minute'
OPENINGS = tuple(map(stamp, ('2024-02-28T23:58', '2024-02-29', '2024-02-29T00:07',
                             '2024-03-01', '2024-03-01T00:06')))
DAYS = tuple(map(stamp, ('2024-02-28', '2024-02-29', '2024-02-29', '2024-03-01', '2024-03-01')))
HISTORY_INDEX = 2
PAUSE_AFTER = 2
HISTORICAL = ((4,) * 5, (OPENINGS[-1],) * 5, DAYS, (0, 0, 0, 1, 1))
REPLAY = ((2, 2, 2, 3, 4), (*((OPENINGS[2],) * 3), *OPENINGS[3:]), DAYS, (0, 1, 1, 1, 1))


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def tape():
    from packages.contracts.realtime import MarketEvent

    rows = []
    for index, time in enumerate(OPENINGS):
        if index == 3:
            rows.append(MarketEvent(len(rows), SPARSE_ID, 'open', time, time, 0, 10., 10., 10., 10., 0.))
            rows.append(MarketEvent(len(rows), SPARSE_ID, 'update', time, time + 30_000,
                                    1, 10., 11., 9., 10., 5.))
        revision = 2 if index == 3 else 0
        rows.append(MarketEvent(len(rows), SPARSE_ID, 'close', time, time + 60_000,
                                revision, 10., 11., 9., 10., 10.))
    return tuple(rows)


def fixtures():
    datasets = {'sparse': ('1', OPENINGS),
                'daily': ('D', tuple(map(stamp, ('2024-02-28', '2024-03-01')))),
                'weekly': ('W', tuple(map(stamp, ('2024-02-26', '2024-03-04')))),
                'monthly': ('M', tuple(map(stamp, ('2024-02-01', '2024-03-01'))))}
    files = {}
    for name, (period, opened) in datasets.items():
        dataset = bars(period, opened)
        dataset.update(context_id=SPARSE_ID if name == 'sparse' else 'clock-' + name, symbol=SYMBOL)
        files[name + '.json'] = canonical(dataset) + b'\n'
    cases = []

    def add(version, name, body, *, dataset='sparse', values=None):
        case_id = f'v{version}-{name}'
        filename = case_id + '.pine'
        files[filename] = source(version, body).encode()
        expected = (dict(status='rejected', category='PROFILE_UNCOVERED', code='ENVIRONMENT_VERSION')
                    if values is None else dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                        observation_count=len(values[0]), qualifiers=['series'] * len(values),
                        outputs=[dict(title='', type='int', values=row) for row in values]))
        cases.append(dict(case_id=case_id, language_version=version, source=filename,
                          bars=dataset + '.json', expected=expected))

    for version in range(1, 7):
        if version < 5:
            add(version, 'last-time-unavailable', 'plot(last_bar_time)')
            add(version, 'last-index-unavailable', 'plot(last_bar_index)')
        else:
            add(version, 'fixed-snapshot-end',
                'plot(last_bar_index)\nplot(last_bar_time)\nplot(last_bar_index-bar_index)',
                values=(*HISTORICAL[:2], (4, 3, 2, 1, 0)))
        if version < 4:
            add(version, 'trading-day-unavailable', 'plot(time_tradingday)')
        else:
            add(version, 'minute-trading-day', 'plot(time_tradingday)', values=(DAYS,))
            for dataset, expected in (
                ('daily', ('2024-02-28', '2024-03-01')),
                ('weekly', ('2024-03-03', '2024-03-10')),
                ('monthly', ('2024-02-29', '2024-03-31')),
            ):
                add(version, dataset + '-trading-day', 'plot(time_tradingday)', dataset=dataset,
                    values=(tuple(map(stamp, expected)),))
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Frozen sparse-dataset end, zero-based received bar indices and UTC calendar days.'))) + b'\n'
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
    print('Chart clock fixtures verified.')


if __name__ == '__main__':
    main()
