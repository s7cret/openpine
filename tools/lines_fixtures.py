"""Generate fixed bar-index line fixtures with independent expectations."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

MOVING = """var line track = na
var label tag = label.new(na, na, "moving")
if bar_index == 0
    track := line.new(na, na, na, na)
alias = track
line.set_xy1(alias, 0, 10)
line.set_xy2(alias, bar_index, close)
label.set_xy(tag, bar_index, close)
if bar_index == 1
    temporary = line.new(x1=0, y1=20, x2=0, y2=25)
    line.delete(temporary)
    line.set_xy2(temporary, 10, 11)
    line.delete(temporary)
line.delete(na)
"""


def source(version, body=MOVING):
    declaration = 'indicator' if version >= 5 else 'study'
    return f'//@version={version}\n{declaration}("Lines", overlay=true)\n{body}'


def strategy_source(version, *, with_drawings=True, channel=False, on_tick=False, evict=False):
    tick = ', calc_on_every_tick=true' if on_tick else ''
    capacity = ', max_lines_count=2' if evict else ''
    direction = 'true' if version == 1 else 'strategy.long'
    text = f'''//@version={version}
strategy("Limit levels", default_qty_type=strategy.fixed, initial_capital=1000, overlay=true{tick}{capacity})
if barstate.isconfirmed and bar_index == 0
    strategy.entry("L", {direction}, limit=21)
if barstate.isconfirmed and bar_index == 2
    strategy.close("L")
plot(strategy.equity)
'''
    if not with_drawings:
        return text
    text += 'var line level = line.new(0, 21, 3, 21, extend=extend.right)\n'
    if not channel:
        end = 'level.set_x2(bar_index)' if version >= 5 else 'line.set_x2(level, bar_index)'
        return text + f'if bar_index > 0\n    {end}\n'
    text += '''var line lower = line.new(0, 6, 3, 6, extend=extend.right)
var linefill band = linefill.new(level, lower, #12345680)
var label tag = label.new(0, 21, "Limit submitted")
var table dashboard = table.new(position.top_right, 1, 1)
if bar_index == 1
    lower.set_xy2(3, close - 2)
    band.set_color(#33669980)
    tag.set_text("Filled")
if barstate.islast
    dashboard.cell(0, 0, str.tostring(strategy.equity))
'''
    if evict:
        text += 'if bar_index == 2\n    line.new(0, high, 3, high)\n'
    return text


LIMIT_LEVEL = dict(id=1, x1=0, y1=21., x2=3, y2=21., extend='right')


def strategy_level():
    return dict(status='executed', title='Limit levels', shorttitle='', overlay=True, observation_count=4,
        outputs=[dict(title='', type='float', values=[1000., 1001., 1011., 1020.])], qualifiers=['series'],
        labels=[], lines=[LIMIT_LEVEL], pending_entries=[],
        simulation_statistics=dict(cash='1020', equity='1020', position_size='0', position_avg_price=None,
            gross_realized='20', unrealized='0', commission='0', orders=2, fills=2, trades=1,
            cash_exact='1020', equity_exact='1020', gross_realized_exact='20', unrealized_exact='0',
            allocated_entry_commission='0', entry_commission='0', exit_commission='0', closed_positions=1,
            protections=[], entries=[]))


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

    def executed(lines, labels):
        return dict(status='executed', title='Lines', shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], lines=lines, labels=labels)

    for version in range(1, 4):
        add(version, 'call-version', 'line.new(0, 1, 2, 3)\n', rejected('LINE_VERSION'))
        add(version, 'type-version', 'line item = na\nplot(close)\n', rejected('LINE_VERSION'))
        add_case(version, 'strategy-level', strategy_source(version), rejected('LINE_VERSION'))
    for version in range(4, 7):
        add_case(version, 'strategy-level', strategy_source(version), strategy_level())
        add(version, 'moving', MOVING, executed(
            [dict(id=1, x1=0, y1=10., x2=3, y2=40.)], [dict(id=1, x=3, y=40., text='moving')]))
        add(version, 'missing-endpoint', 'var line item = line.new(na, 10, 1, 20)\nline.set_xy1(na, 0, 1)\n',
            executed([dict(id=1, x1=None, y1=10., x2=1, y2=20.)], []))
        add(version, 'wrong-kind', 'line.delete(label.new(0, close))\n', rejected('ARGUMENT_TYPE'))
        add(version, 'outside-slice', 'line.new(0, 1, 2, 3, xloc="bar_time")\n', rejected('DRAWING_XLOC'))
        if version >= 5:
            add(version, 'varip-line', 'varip line item = line.new(0, 1, 2, 3)\n',
                rejected('LINE_BINDING', 'PROFILE_UNCOVERED'))
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='lines.test',
        symbol='TEST:LINES', timeframe='1', bars=[
            dict(time=i * 60000, open=c - 1, high=c + 2, low=c - 2, close=c, volume=1.)
            for i, c in enumerate((10., 20., 30., 40.))])) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Fixed OHLC, manual endpoint and alias expectations, explicit Pine version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/lines'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Line fixtures verified.')


if __name__ == '__main__':
    main()
