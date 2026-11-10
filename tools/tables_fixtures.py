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


CELL_LAYOUT = """var table grid = table.new(position.top_left, 3, 2, frame_color=#AACCEE, frame_width=2, border_color=#557799, border_width=1OVERLAY)
if bar_index == 0
    table.cell(grid, 0, 0, "Value", 25.5, 20, color.white, text.align_left, text.align_top, FIRST_SIZE, #12345680FIRST_TAIL)
    table.cell(grid, 0, 1, "Column", width=35)
    table.cell(grid, 1, 1, "Row", height=30)
    table.cell(grid, 2, 0, "Old", width=17, height=5)
if bar_index == 1
    table.cell_set_width(grid, 0, 0, close < 20 ? 40.25 : 20.5)
    table.cell_set_height(grid, 0, 0, 25)
    table.cell_set_text_halign(grid, 0, 0, text.align_right)
    table.cell_set_text_valign(grid, 0, 0, text.align_bottom)
    table.cell_set_text_size(grid, 0, 0, NEXT_SIZE)
    table.cell_set_text(grid, 0, 0, close < 20 ? "Preview" : "Confirmed")
    table.cell_set_height(grid, 1, 0, 9.5)
    UPDATE_LAYOUT
if bar_index == 2
    table.clear(grid, 2, 0)
    table.cell_set_text_halign(grid, 2, 0, text.align_left)
if bar_index == 3
    table.cell(grid, 2, 0, "reset")
"""


MERGE = """var table grid = table.new(position.top_left, 3, 3, frame_color=#AACCEE, frame_width=2, border_color=#557799, border_width=1, force_overlay=true)
if bar_index == 0
    table.merge_cells(grid, 0, 0, 1, 1)
    table.cell(grid, 0, 0, "Dashboard", width=95, height=95, text_color=color.white, bgcolor=#112233, tooltip="<b>Merged</b>")
    table.cell(grid, 1, 1, "Hidden", width=90, height=90, bgcolor=#FF0000)
    table.cell(grid, 2, 0, "A", width=20, height=12)
    table.cell(grid, 2, 1, "B", height=18)
    table.cell(grid, 0, 2, "C", width=15, height=10)
    table.cell(grid, 1, 2, "D", width=25)
    table.cell(grid, 2, 2, "Status")
if bar_index == 1
    table.cell_set_text(grid, 1, 1, "Hidden update")
    table.cell_set_text(grid, 0, 0, close < 20 ? "Open" : "Confirmed")
    if close < 20
        table.merge_cells(grid, 2, 0, 2, 1)
if bar_index == 2
    table.clear(grid, 0, 0)
if bar_index == 3
    table.cell(grid, 0, 0, "Restored", bgcolor=#334455, tooltip="<b>Merged</b>")
"""

MERGE_AFTER = """var table grid = table.new(position.middle_center, 3, 1)
var table solo = table.new(position.bottom_left, 1, 1)
if barstate.isfirst
    table.cell(grid, 0, 0, "Whole header")
    table.cell(grid, 2, 0, "Hidden")
    table.merge_cells(table_id=grid, start_column=0, start_row=0, end_column=2, end_row=0)
    table.merge_cells(solo, 0, 0, 0, 0)
    table.cell(solo, 0, 0, "Solo")
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


def cell_layout_source(version):
    tail = ', "<b>Value</b>", font.family_default' if version >= 5 else ''
    updates = []
    if version >= 5:
        updates.extend(('table.cell_set_tooltip(grid, 0, 0, close < 20 ? "Preview tip" : "<b>Confirmed</b>")',
                        'table.cell_set_text_font_family(grid, 0, 0, font.family_monospace)',
                        'table.cell_set_tooltip(grid, 1, 0, na)'))
    if version == 6:
        tail += ', text.format_italic'
        updates.append('table.cell_set_text_formatting(grid, 0, 0, text.format_bold + text.format_italic)')
    return (CELL_LAYOUT.replace('OVERLAY', ', force_overlay=true' if version >= 5 else '')
            .replace('FIRST_SIZE', '12' if version == 6 else 'size.small').replace('FIRST_TAIL', tail)
            .replace('NEXT_SIZE', '18' if version == 6 else 'size.large')
            .replace('    UPDATE_LAYOUT\n', ''.join(f'    {row}\n' for row in updates)))


def layout_table(version, phase):
    def cell(column, row, text='', text_color='#363A45FF', bgcolor=None):
        return dict(column=column, row=row, text=text, text_color=text_color, bgcolor=bgcolor)

    def layout(column, row, text='', text_color='#363A45FF', bgcolor=None, **options):
        return dict(cell(column, row, text, text_color, bgcolor), **{
            **dict(width=0.0, height=0.0, text_halign='center', text_valign='center', text_size='normal',
                   tooltip='', text_font_family='default', text_formatting=0), **options})

    if phase == 'first':
        first = layout(0, 0, 'Value', '#FFFFFFFF', '#12345680', width=25.5, height=20.0,
                       text_halign='left', text_valign='top', text_size=12 if version == 6 else 'small',
                       tooltip='<b>Value</b>' if version >= 5 else '', text_formatting=2 if version == 6 else 0)
    else:
        preview = phase == 'preview'
        tip = 'Preview tip' if preview else '<b>Confirmed</b>'
        first = layout(0, 0, 'Preview' if preview else 'Confirmed', '#FFFFFFFF', '#12345680',
                       width=40.25 if preview else 20.5, height=25.0, text_halign='right', text_valign='bottom',
                       text_size=18 if version == 6 else 'large', tooltip=tip if version >= 5 else '',
                       text_font_family='monospace' if version >= 5 else 'default', text_formatting=3 if version == 6 else 0)
    cells = [first]
    if phase != 'first':
        cells.append(layout(1, 0, height=9.5))
    cells.extend((cell(2, 0, 'reset') if phase == 'final' else layout(2, 0, 'Old', width=17.0, height=5.0),
                  layout(0, 1, 'Column', width=35.0), layout(1, 1, 'Row', height=30.0)))
    record = dict(id=1, position='top_left', columns=3, rows=2, bgcolor=None, cells=cells,
                  frame_color='#AACCEEFF', frame_width=2, border_color='#557799FF', border_width=1)
    return dict(record, force_overlay=True) if version >= 5 else record


def merge_range(start_column, start_row, end_column, end_row):
    return dict(start_column=start_column, start_row=start_row, end_column=end_column, end_row=end_row)


def merge_table(phase):
    def cell(column, row, text, *, color='#363A45FF', bgcolor=None, **style):
        base = dict(column=column, row=row, text=text, text_color=color, bgcolor=bgcolor)
        if style:
            return dict(base, **{**dict(width=0.0, height=0.0, text_halign='center', text_valign='center',
                text_size='normal', tooltip='', text_font_family='default', text_formatting=0), **style})
        return base

    cells = []
    if phase == 'final':
        cells.append(cell(0, 0, 'Restored', bgcolor='#334455FF', tooltip='<b>Merged</b>'))
    elif phase != 'cleared':
        text = {'first': 'Dashboard', 'preview': 'Open', 'confirmed': 'Confirmed'}[phase]
        cells.append(cell(0, 0, text, color='#FFFFFFFF', bgcolor='#112233FF', width=95.0, height=95.0,
                          tooltip='<b>Merged</b>'))
    cells.extend((cell(2, 0, 'A', width=20.0, height=12.0),
                  cell(1, 1, 'Hidden' if phase == 'first' else 'Hidden update', bgcolor='#FF0000FF',
                       width=90.0, height=90.0),
                  cell(2, 1, 'B', height=18.0),
                  cell(0, 2, 'C', width=15.0, height=10.0), cell(1, 2, 'D', width=25.0), cell(2, 2, 'Status')))
    ranges = [merge_range(0, 0, 1, 1)]
    if phase == 'preview':
        ranges.append(merge_range(2, 0, 2, 1))
    return dict(id=1, position='top_left', columns=3, rows=3, bgcolor=None, cells=cells, force_overlay=True,
                frame_color='#AACCEEFF', frame_width=2, border_color='#557799FF', border_width=1, merges=ranges)


def merged_after_tables():
    header = dict(column=0, row=0, text='Whole header', text_color='#363A45FF', bgcolor=None)
    hidden = dict(header, column=2, text='Hidden')
    return [dict(id=1, position='middle_center', columns=3, rows=1, bgcolor=None, cells=[header, hidden],
                 merges=[merge_range(0, 0, 2, 0)]),
            dict(id=2, position='bottom_left', columns=1, rows=1, bgcolor=None, cells=[dict(header, text='Solo')])]


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
    for version in range(1, 4):
        add(version, 'cell-layout-version', 'table.cell_set_width(na, 0, 0, 15)\n', rejected('TABLE_VERSION'))
    for version in range(4, 7):
        add(version, 'cell-layout', cell_layout_source(version), {**executed([layout_table(version, 'final')]),
            'overlay': False}, overlay=False)
    item = 'table.new(position.top_left, 1, 1)'
    for version in (4, 5):
        add(version, 'cell-point-size-version', f'table.cell_set_text_size({item}, 0, 0, 12)\n', rejected('ARGUMENT_TYPE'))
    add(4, 'cell-tooltip-version', f'table.cell({item}, 0, 0, tooltip="tip")\n', rejected('TABLE_CELL_LAYOUT_VERSION'))
    add(4, 'cell-font-version', f'table.cell_set_text_font_family({item}, 0, 0, "monospace")\n',
        rejected('TABLE_CELL_LAYOUT_VERSION'))
    add(5, 'cell-format-version', f'table.cell({item}, 0, 0, text_formatting=0)\n',
        rejected('TABLE_CELL_LAYOUT_VERSION'))
    add(5, 'cell-format-setter-version', f'table.cell_set_text_formatting({item}, 0, 0, 0)\n',
        rejected('TABLE_CELL_LAYOUT_VERSION'))
    for name, body, code in (
            ('cell-layout-bounds', f'table.cell_set_height({item}, 1, 0, 10)', 'TABLE_INDEX'),
            ('cell-layout-width', f'table.cell({item}, 0, 0, width=101)', 'TABLE_CELL_LAYOUT'),
            ('cell-layout-axis', f'table.cell_set_text_halign({item}, 0, 0, text.align_bottom)', 'TABLE_CELL_LAYOUT'),
            ('cell-layout-points', f'table.cell_set_text_size({item}, 0, 0, -1)', 'TABLE_CELL_LAYOUT')):
        add(6, name, body + '\n', rejected(code, 'RUNTIME_ERROR'))
    for version in range(1, 5):
        add(version, 'merge-version', 'table.merge_cells(na, 0, 0, 1, 0)\n',
            rejected('TABLE_VERSION' if version < 4 else 'TABLE_MERGE_VERSION'))
    for version in (5, 6):
        add(version, 'merge', MERGE, {**executed([merge_table('final')]), 'overlay': False}, overlay=False)
        add(version, 'merge-after', MERGE_AFTER, executed(merged_after_tables()))
    add(6, 'merge-clear', MERGE.split('if bar_index == 3')[0],
        {**executed([merge_table('cleared')]), 'overlay': False}, overlay=False)
    preamble = 'var table grid = table.new(position.top_left, 3, 3)\n'
    for version, name, body, code, category in (
            (5, 'merge-bounds', 'table.merge_cells(grid, 0, 0, 3, 1)', 'TABLE_INDEX', 'RUNTIME_ERROR'),
            (6, 'merge-order', 'table.merge_cells(grid, 0, 1, 2, 0)', 'TABLE_MERGE_RANGE', 'RUNTIME_ERROR'),
            (6, 'merge-overlap', 'table.merge_cells(grid, 0, 0, 1, 1)\ntable.merge_cells(grid, 1, 1, 2, 2)',
             'TABLE_MERGE_OVERLAP', 'RUNTIME_ERROR'),
            (5, 'merge-repeat', 'table.merge_cells(grid, 0, 0, 1, 1)\ntable.merge_cells(grid, 0, 0, 1, 1)',
             'TABLE_MERGE_OVERLAP', 'RUNTIME_ERROR'),
            (6, 'merge-missing-argument', 'table.merge_cells(grid, 0, 0, 1)', 'MISSING_ARGUMENT', 'LANGUAGE_INVALID'),
            (6, 'merge-coordinate-type', 'table.merge_cells(grid, 0, 0, 1, 1.5)', 'ARGUMENT_TYPE', 'LANGUAGE_INVALID')):
        add(version, name, preamble + body + '\n', rejected(code, category))
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
