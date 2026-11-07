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
