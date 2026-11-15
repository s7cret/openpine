"""Manual change and event-color expectations with an independent replay tape."""

import argparse
from dataclasses import replace

from packages.contracts.contexts import MarketContext
from packages.contracts.realtime import MarketEvent
from packages.contracts.wire import canonical
from tools.specification import ROOT


DIRECTORY = 'conformance/change-valuewhen'
CASES = DIRECTORY + '/cases.json'
EXAMPLE = 'examples/change-valuewhen/direction-color.pine'
TITLE = 'Change and event values'
CLOSES = (10., 9., 8., 7., 8., 10., 11., 11.)
HIGHER_CLOSES = (8., 5., 9., 10.)
PRIMARY = MarketContext('direction-primary', 'TEST:DIRECTION', '1', 'recorded.1')
HIGHER = MarketContext('direction-higher', PRIMARY.symbol, '2', PRIMARY.source)
GREEN, RED = '#12AB5680', '#D0405040'
HISTORICAL = (None, None, None, -3., -3., 4., 4., 1.)
REPLAY = (None, None, -3., -3., 4., 4., 1., 1.)
HISTORICAL_COLORS = (None, None, None, RED, RED, GREEN, GREEN, GREEN)
REPLAY_COLORS = (None, None, RED, RED, GREEN, GREEN, GREEN, GREEN)
ALERT = (22, 'Direction turned up: 4')


def source(version, body):
    header = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{header}("{TITLE}")\n{body}\n'


def tape():
    rows = []
    for context, values in ((PRIMARY, CLOSES), (HIGHER, HIGHER_CLOSES)):
        duration = int(context.timeframe) * 60_000
        for index, close in enumerate(values):
            start = index * duration
            preview = context == HIGHER and index == 1
            spike = 12. if preview else close
            update = 3 * duration // 4 if preview else duration // 2
            phases = ((0, 'open', 0, close, close), (1, 'update', update, spike, spike),
                      (2, 'close', duration, close, spike))
            for step, kind, elapsed, price, high in phases:
                rows.append(MarketEvent(0, context.context_id, kind, start, start + elapsed,
                                        step, close, high, close, price, float(step)))
    rows.sort(key=lambda e: (e.event_time, 2 if e.kind == 'open' else e.context_id == PRIMARY.context_id))
    return tuple(replace(event, sequence=i) for i, event in enumerate(rows))


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        identifier = f'v{version}-{name}'
        path = identifier + '.pine'
        files[path] = source(version, body).encode()
        cases.append(dict(case_id=identifier, language_version=version, source=path, bars='bars.json', expected=expected))

    def output(base, values, **style):
        return dict(title='', type=base, values=values, **style)

    def executed(*outputs):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=False,
                    observation_count=len(CLOSES), qualifiers=['series'] * len(outputs), outputs=outputs)

    def rejected(code):
        return dict(status='rejected', category='LANGUAGE_INVALID', code=code)

    for version in range(1, 7):
        change = 'change' if version < 5 else 'ta.change'
        valuewhen = 'valuewhen' if version < 5 else 'ta.valuewhen'
        delta = (None, -1, -1, -1, 1, 2, 1, 0)
        floats = tuple(float(value) if value is not None else None for value in delta)
        add(version, 'change-default', '\n'.join(f'plot({change}({src}{length}))'
                                                 for src in ('close', 'int(close)') for length in ('', ',1')),
            executed(output('float', floats), output('float', floats), output('int', delta), output('int', delta)))
        expected = ((0 if version == 6 else -1), 0, 1, 0, 0, 1, 0, 0)
        bool_source = f'a={change}(close>8)\nb={change}(close>8,1)\n'
        bool_plots = ('plot(a?1:0)\nplot(b?1:0)' if version == 6 else
                      'plot(na(a)?-1:a?1:0)\nplot(na(b)?-1:b?1:0)')
        add(version, 'change-bool', bool_source + bool_plots,
            executed(output('int', expected), output('int', expected)))
        add(version, 'change-length-and-holes', f'src=bar_index==2?na:close\nplot({change}(src,bar_index%2+1))',
            executed(output('float', (None, None, None, -2., 1., 3., 1., 1.))))
        if version < 5:
            add(version, 'color-unavailable', 'plot(close,"",valuewhen(true,#12345680,0))', rejected('ARGUMENT_TYPE'))
            continue
        old, new = '#12345680', '#ABCDEF40'
        body = (f'event=bar_index%2==1\ncolor c=bar_index==3?na:bar_index<3?{old}:{new}\n'
                f'plot(close,color={valuewhen}(event,c,0))\n'
                f'plot(close,color={valuewhen}(occurrence=1,source=c,condition=event))')
        add(version, 'color-events', body,
            executed(output('float', CLOSES, colors=(None, old, old, None, None, new, new, new), linewidth=1, style='line'),
                     output('float', CLOSES, colors=(None, None, None, old, old, None, None, new), linewidth=1, style='line')))
        add(version, 'color-series-occurrence', 'plot(close,color=ta.valuewhen(true,#123456,bar_index))',
            rejected('ARGUMENT_QUALIFIER'))
        add(version, 'bare-na', 'plot(ta.valuewhen(true,na,0))', executed(output('float', (None,) * 8)))

    bars = [dict(time=i * 60_000, open=x, high=x, low=x, close=x, volume=2.) for i, x in enumerate(CLOSES)]
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id=PRIMARY.context_id,
                                       symbol=PRIMARY.symbol, timeframe='1', bars=bars)) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Manual differences and retained colors, including a true event with missing color.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    directory = ROOT / DIRECTORY
    check = parser.parse_args(argv).check
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, data in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == data, name
        else:
            path.write_bytes(data)
    print('Change and event-value fixtures verified.')


if __name__ == '__main__':
    main()
