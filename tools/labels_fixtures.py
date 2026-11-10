"""Generate independently specified historical label fixtures."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

MOVING = """var label moving = na
if bar_index == 0
    moving := label.new(na, na)
    label.new(1, close, "anchor")
alias = moving
label.set_xy(alias, bar_index, close)
label.set_text(alias, bar_index >= 2 ? "late" : "early")
if bar_index == 1
    temporary = label.new(bar_index, close, "temporary")
    label.delete(temporary)
    label.delete(temporary)
    label.set_text(temporary, "ignored")
label.delete(na)
"""


def source(version, body=MOVING):
    declaration = 'indicator' if version >= 5 else 'study'
    return f'//@version={version}\n{declaration}("Labels", overlay=true)\n{body}'


def strategy_source(version, *, with_labels=True, with_table=True, on_tick=False):
    limits = ', max_labels_count=2' if version >= 4 and with_labels else ''
    tick = ', calc_on_every_tick=true' if on_tick else ''
    direction = 'true' if version == 1 else 'strategy.long'
    text = f'''//@version={version}
strategy("Signal labels", default_qty_type=strategy.fixed, initial_capital=1000, overlay=true{limits}{tick})
if barstate.isconfirmed and bar_index == 0
    strategy.entry("L", {direction})
if barstate.isconfirmed and bar_index == 2
    strategy.close("L")
plot(strategy.equity)
'''
    if with_labels:
        xy = 'status.set_xy(bar_index, close)' if version >= 5 else 'label.set_xy(status, bar_index, close)'
        update = 'status.set_text("Open")' if version >= 5 else 'label.set_text(status, "Open")'
        text += f'''var label status = na
if bar_index == 0
    status := label.new(bar_index, close, "Pending")
    label.new(bar_index, high, "Entry submitted")
if bar_index == 1
    {xy}
    {update}
if bar_index == 2
    label.new(bar_index, high, "Exit submitted")
'''
    if with_table:
        tostring = 'str.tostring' if version >= 5 else 'tostring'
        text += f'''var table dashboard = table.new(position.top_right, 1, 1)
if barstate.islast
    table.cell(dashboard, 0, 0, {tostring}(strategy.equity))
'''
    return text


def fixtures():
    files, cases = {}, []

    def add_case(version, name, text, expected):
        case_id = f'v{version}-{name}'
        filename = case_id + '.pine'
        files[filename] = text.encode()
        cases.append(dict(case_id=case_id, language_version=version, source=filename,
                          bars='bars.json', expected=expected))

    def add(version, name, body, expected):
        add_case(version, name, source(version, body), expected)

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 4):
        add(version, 'call-version', 'label.new(0, close, "v4")\n', rejected('LABEL_VERSION'))
        add(version, 'type-version', 'label item = na\nplot(close)\n', rejected('LABEL_VERSION'))
    for version in range(4, 7):
        add(version, 'moving', MOVING, dict(status='executed', title='Labels', shorttitle='', overlay=True,
            observation_count=4, outputs=[], qualifiers=[], labels=[
                dict(id=1, x=3, y=40., text='late'), dict(id=2, x=1, y=10., text='anchor')]))
        add(version, 'wrong-id', 'label.delete(1)\n', rejected('ARGUMENT_TYPE'))
        add(version, 'outside-slice', 'label.new(0, close, "x", xloc="bar_time")\n', rejected('DRAWING_XLOC'))
        if version >= 5:
            add(version, 'varip-label', 'varip label item = label.new(bar_index, close)\n',
                rejected('LABEL_BINDING', 'PROFILE_UNCOVERED'))
    for version in range(1, 4):
        add_case(version, 'strategy-label', strategy_source(version, with_table=False), rejected('LABEL_VERSION'))
    for version in range(4, 7):
        expected = dict(status='executed', title='Signal labels', shorttitle='', overlay=True,
            observation_count=4, outputs=[dict(title='', type='float', values=[1000., 1001., 1011., 1020.])],
            qualifiers=['series'], labels=[dict(id=2, x=0, y=12., text='Entry submitted'),
                dict(id=3, x=2, y=32., text='Exit submitted')], pending_entries=[],
            simulation_statistics=dict(cash='1020', equity='1020', position_size='0', position_avg_price=None,
                gross_realized='20', unrealized='0', commission='0', orders=2, fills=2, trades=1))
        add_case(version, 'strategy-label', strategy_source(version, with_table=False), expected)
        add_case(version, 'strategy-label-table', strategy_source(version), {**expected, 'lines': [], 'boxes': [],
            'tables': [dict(id=1, position='top_right', columns=1, rows=1, bgcolor=None,
                cells=[dict(column=0, row=0, text='1020', text_color='#363A45FF', bgcolor=None)])]})
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='labels.test',
        symbol='TEST:LABELS', timeframe='1', bars=[
            dict(time=i * 60000, open=c - 1, high=c + 2, low=c - 2, close=c, volume=1.)
            for i, c in enumerate((10., 20., 30., 40.))])) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Manual fixed OHLC, creation order, alias updates, and deletion expectations.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/labels'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Label fixtures verified.')


if __name__ == '__main__':
    main()
