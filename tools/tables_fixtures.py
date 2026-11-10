"""Hand-authored cases for current pane table state and version boundaries."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

STATUS = """var table dash = table.new(position.top_right, 2, 2, bgcolor=#12345680)
var box zone = box.new(0, 50, 3, 5)
table alias = dash
if barstate.isfirst
    table.cell(dash, 0, 0, "Price", text_color=color.white)
    table.cell(dash, 0, 1, "remove")
    table.cell(dash, 1, 1, "remove")
if barstate.islast
    table.cell(alias, 1, 0, TOSTRING(close, "#"), text_color=color.white, bgcolor=close >= 20 ? #228844FF : #AA2222FF)
    table.clear(dash, 0, 1, 1, 1)
"""

REPLACE = """var table old = table.new(position.middle_center, 2, 1)
if barstate.isfirst
    table.cell(old, 0, 0, "previous")
    fresh = table.new(position.middle_center, 2, 1)
    table.cell(fresh, 0, 0, "replacement", text_color=#FFFFFFFF, bgcolor=#12345680)
    table.cell(fresh, 0, 0, "replacement")
    table.cell(fresh, 1, 0, "clear")
    table.clear(fresh, 1, 0)
    table.cell(old, 0, 0, "stale")
    table.delete(old)
    extra = table.new(position.bottom_left, 1, 1)
    table.cell(extra, 0, 0)
    table.delete(extra)
table.delete(na)
"""

OVERLAY = """var table panel = table.new(position.bottom_left, 1, 1, force_overlay=false)
var table old = table.new(position.top_right, 1, 1)
var table price = na
table.cell(panel, 0, 0, "Pane")
if bar_index == 1
    price := table.new(position.top_right, 1, 1, bgcolor=#12345680, force_overlay=true)
table.cell(old, 0, 0, "Old")
if bar_index >= 1
    table.cell(price, 0, 0, TOSTRING(close, "#"), text_color=color.white)
"""

STYLE = """var table dash = table.new(position.top_right, 2, 2, bgcolor=#223344, frame_color=#AA2200, frame_width=2, border_color=#556677, border_width=1OVERLAY)
if bar_index == 0
    table.cell(dash, 0, 0, "Seed", text_color=#00FF00, bgcolor=#12345680)
    table.cell_set_text(dash, 0, 1, "new")
if bar_index == 1
    table.set_position(dash, position.bottom_left)
    table.set_bgcolor(dash, #112233)
    table.set_frame_color(dash, #AACCEE)
    table.set_frame_width(dash, 4)
    table.set_border_color(dash, #557799)
    table.set_border_width(dash, 3)
    table.cell_set_text(dash, 0, 0, close < 20 ? "preview" : "close")
    table.cell_set_text_color(dash, 0, 0, #FFFFFF)
    table.cell_set_bgcolor(dash, 1, 0, #345678)
if bar_index == 2
    table.clear(dash, 0, 1)
    table.cell_set_bgcolor(dash, 0, 1, #778899)
if bar_index == 3
    table.cell(dash, 0, 0, "reset")
"""

RELOCATE = """var table first = table.new(position.top_left, 1, 1)
var table second = table.new(position.bottom_right, 1, 1)
if bar_index == 0
    table.cell(first, 0, 0, "first")
    table.cell(second, 0, 0, "second")
if bar_index == 1
    table.set_position(first, position.bottom_right)
    third = table.new(position.top_left, 1, 1)
    table.set_frame_width(third, 2)
    table.set_frame_color(third, #FF0000)
    table.cell_set_text(third, 0, 0, "third")
    table.set_position(third, position.bottom_right)
    table.cell_set_text(first, 0, 0, "stale")
    table.cell_set_text(second, 0, 0, "stale")
    table.set_position(third, position.middle_center)
"""


ZONE = dict(id=1, left=0, top=50., right=3, bottom=5., border_color='#2196F3FF', bgcolor='#2196F3FF')


def source(version, body=STATUS, *, overlay=True):
    declaration = 'indicator' if version >= 5 else 'study'
    body = body.replace('TOSTRING', 'str.tostring' if version >= 5 else 'tostring')
    return f'//@version={version}\n{declaration}("Tables", overlay={str(overlay).lower()})\n{body}'


def status_table(price='40', color='#228844FF'):
    return dict(id=1, position='top_right', columns=2, rows=2, bgcolor='#12345680', cells=[
        dict(column=0, row=0, text='Price', text_color='#FFFFFFFF', bgcolor=None),
        dict(column=1, row=0, text=price, text_color='#FFFFFFFF', bgcolor=color)])


def overlay_tables(price=None):
    def record(identity, position, text, text_color='#363A45FF', bgcolor=None):
        return dict(id=identity, position=position, columns=1, rows=1, bgcolor=bgcolor,
                    cells=[dict(column=0, row=0, text=text, text_color=text_color, bgcolor=None)])

    panel = record(1, 'bottom_left', 'Pane')
    if price is None:
        return [panel, record(2, 'top_right', 'Old')]
    forced = record(3, 'top_right', price, '#FFFFFFFF', '#12345680')
    return [panel, {**forced, 'force_overlay': True}]


def style_source(version):
    return STYLE.replace('OVERLAY', ', force_overlay=true' if version >= 5 else '')


def styled_table(version, phase):
    def cell(column, row, text='', text_color='#363A45FF', bgcolor=None):
        return dict(column=column, row=row, text=text, text_color=text_color, bgcolor=bgcolor)

    record = dict(id=1, position='top_right', columns=2, rows=2, bgcolor='#223344FF',
                  frame_color='#AA2200FF', frame_width=2, border_color='#556677FF', border_width=1)
    if version >= 5:
        record['force_overlay'] = True
    if phase == 'first':
        return dict(record, cells=[cell(0, 0, 'Seed', '#00FF00FF', '#12345680'), cell(0, 1, 'new')])
    record.update(position='bottom_left', bgcolor='#112233FF', frame_color='#AACCEEFF', frame_width=4,
                  border_color='#557799FF', border_width=3)
    first = cell(0, 0, 'reset') if phase == 'final' else cell(0, 0, phase, '#FFFFFFFF', '#12345680')
    last = cell(0, 1, bgcolor='#778899FF') if phase == 'final' else cell(0, 1, 'new')
    return dict(record, cells=[first, cell(1, 0, bgcolor='#345678FF'), last])


RELOCATED = dict(id=3, position='middle_center', columns=1, rows=1, bgcolor=None,
                 frame_color='#FF0000FF', frame_width=2, border_color=None, border_width=0,
                 cells=[dict(column=0, row=0, text='third', text_color='#363A45FF', bgcolor=None)])


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected, *, overlay=True):
        case_id = f'v{version}-{name}'
        filename = case_id + '.pine'
        files[filename] = source(version, body, overlay=overlay).encode()
        cases.append(dict(case_id=case_id, language_version=version, source=filename,
                          bars='bars.json', expected=expected))

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    def executed(tables, boxes=()):
        return dict(status='executed', title='Tables', shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], labels=[], lines=[], boxes=list(boxes), tables=tables)

    for version in range(1, 4):
        add(version, 'call-version', 'table.new("top_right", 1, 1)\n', rejected('TABLE_VERSION'))
        add(version, 'type-version', 'var table item = na\nplot(close)\n', rejected('TABLE_VERSION'))
        add(version, 'force-overlay-version', 'table.new("top_right", 1, 1, force_overlay=true)\n', rejected('TABLE_VERSION'))
    add(4, 'force-overlay-version', 'table.new(position.top_right, 1, 1, force_overlay=true)\n', rejected('TABLE_OVERLAY_VERSION'))
    for version in range(4, 7):
        add(version, 'status', STATUS, executed([status_table()], [ZONE]))
        add(version, 'replace', REPLACE, executed([dict(id=2, position='middle_center', columns=2, rows=1,
            bgcolor=None, cells=[dict(column=0, row=0, text='replacement', text_color='#363A45FF', bgcolor=None)])]))
        add(version, 'wrong-kind', 'table.delete(box.new(0, 10, 1, 5))\n', rejected('ARGUMENT_TYPE'))
        add(version, 'color-position', 'table.cell(table.new(position.top_right, 1, 1), 0, 0, "", color.blue)\n',
            rejected('TABLE_POSITIONAL_PROFILE', 'PROFILE_UNCOVERED'))
        add(version, 'cell-bounds', 'table.cell(table.new(position.top_right, 1, 1), 1, 0, "")\n',
            rejected('TABLE_INDEX', 'RUNTIME_ERROR'))
        add(version, 'dimensions', 'table.new(position.top_right, 0, 1)\n', rejected('TABLE_VALUE', 'RUNTIME_ERROR'))
    for version in (5, 6):
        add(version, 'force-overlay-mixed', OVERLAY, {**executed(overlay_tables('40')), 'overlay': False}, overlay=False)
        add(version, 'force-overlay-series', 'table.new(position.top_right, 1, 1, force_overlay=close > 0)\n', rejected('ARGUMENT_QUALIFIER'))
        add(version, 'force-overlay-type', 'table.new(position.top_right, 1, 1, force_overlay=1)\n', rejected('ARGUMENT_TYPE'))
        add(version, 'force-overlay-positional', 'table.new(position.top_right, 1, 1, na, true)\n', rejected('TOO_MANY_ARGUMENTS'))
    for version in range(1, 4):
        add(version, 'relocation-version', 'table.set_position(na, "top_left")\n', rejected('TABLE_VERSION'))
        add(version, 'cell-style-version', 'table.cell_set_text(na, 0, 0, "test")\n', rejected('TABLE_VERSION'))
    for version in range(4, 7):
        add(version, 'style', style_source(version), {**executed([styled_table(version, 'final')]), 'overlay': False},
            overlay=False)
        add(version, 'relocation', RELOCATE, executed([RELOCATED]))
    add(6, 'style-width', 'table.new(position.top_right, 1, 1, frame_width=65)\n',
        rejected('TABLE_STYLE', 'RUNTIME_ERROR'))
    add(6, 'cell-style-bounds', 'table.cell_set_bgcolor(table.new(position.top_right, 1, 1), 1, 0, #112233)\n',
        rejected('TABLE_INDEX', 'RUNTIME_ERROR'))
    files['bars.json'] = (ROOT / 'conformance/boxes/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored table state, aliases, cell defaults and rejection expectations.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/tables'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Table fixtures verified.')


if __name__ == '__main__':
    main()
