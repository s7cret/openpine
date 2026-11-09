"""Hand-authored expectations for linefill identity and its two boundaries."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

CHANNEL = """var line upper = line.new(0, 20, 3, 35, extend=extend.right)
var line lower = line.new(0, 5, 3, 15, extend=extend.right)
var linefill area = linefill.new(upper, lower, #12345680)
linefill alias = area
if bar_index == 1
    line.set_xy2(linefill.get_line1(alias), 3, close < 20 ? 30 : 45)
    line.set_xy1(linefill.get_line2(alias), 0, 6)
    linefill.set_color(alias, close < 20 ? #FF000080 : na)
if bar_index == 2
    linefill.set_color(area, #33669980)
"""
DELETE = CHANNEL + """if bar_index == 3
    line.delete(lower)
    linefill.set_color(area, #FFFFFFFF)
"""
REPLACE = """var line first = line.new(0, 20, 3, 20)
var line second = line.new(0, 5, 3, 5)
var linefill old = linefill.new(first, second, #FF000080)
if bar_index == 0
    linefill current = linefill.new(second, first, #22446680)
    linefill.set_color(old, #FFFFFFFF)
    linefill.delete(old)
    line.set_xy2(linefill.get_line1(current), 3, 7)
    line.set_xy2(linefill.get_line2(current), 3, 23)
    linefill invalid = linefill.new(first, first, na)
    linefill.delete(invalid)
"""
UPPER = dict(id=1, x1=0, y1=20., x2=3, y2=45., extend='right')
LOWER = dict(id=2, x1=0, y1=6., x2=3, y2=15., extend='right')
FILL = dict(id=1, line1=1, line2=2, color='#33669980')

CAPACITY_BASE = """var line oldest = line.new(0, 20, 3, 20)
var line second = line.new(0, 5, 3, 5)
"""
CAPACITY_BODY = """if bar_index == 1
    line.set_xy2(oldest, 3, 25)
    line.new(0, 30, 3, 30)
label.new(bar_index, na, "hidden")
box.new(bar_index, close, bar_index + 1, 0)
"""
CAPACITY_POLYLINE = """var array<chart.point> points = array.from(chart.point.from_index(0, 12), chart.point.from_index(3, 18))
polyline.new(points)
"""
INVENTORY = """var line original = line.new(0, 20, 3, 20)
var line replica = line.copy(original)
var linefill area = linefill.new(original, replica, #33669980)
if bar_index == 0
    line.set_xy1(replica, 0, 5)
    line.set_xy2(replica, 3, 10)
if bar_index == 1
    line.set_xy2(replica, 3, 25)
    line.new(0, 30, 3, 30)
if bar_index == 2
    for [index, item] in line.all
        if index == 0
            line.delete(item)
array<line> remaining = line.all
plot(array.size(remaining), "lines")
plot(line.get_price(array.get(remaining, 0), 3), "oldest")
plot(array.size(linefill.all), "fills")
"""
INVENTORY_FINAL = dict(id=3, x1=0, y1=30., x2=3, y2=30.)


def source(version, body=CHANNEL, *, overlay=True, options=''):
    declaration = 'indicator' if version >= 5 else 'study'
    return f'//@version={version}\n{declaration}("Channel", overlay={str(overlay).lower()}{options})\n{body}'


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected, *, options=''):
        case_id = f'v{version}-{name}'
        filename = case_id + '.pine'
        files[filename] = source(version, body, options=options).encode()
        cases.append(dict(case_id=case_id, language_version=version, source=filename,
                          bars='bars.json', expected=expected))

    def rejected(code):
        return dict(status='rejected', category='LANGUAGE_INVALID', code=code)

    def executed(lines, fills):
        return dict(status='executed', title='Channel', shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], labels=[], lines=lines, boxes=[], tables=[], linefills=fills)

    for version in range(1, 5):
        add(version, 'call-version', 'linefill.delete(na)\n', rejected('LINEFILL_VERSION'))
        add(version, 'type-version', 'var linefill item = na\nplot(close)\n', rejected('LINEFILL_VERSION'))
    for version in (5, 6):
        add(version, 'channel', CHANNEL, executed([UPPER, LOWER], [FILL]))
        add(version, 'delete', DELETE, executed([UPPER], []))
        add(version, 'explicit-delete', CHANNEL + """if bar_index == 3
    linefill.delete(area)
    linefill.set_color(area, #FFFFFFFF)
""", executed([UPPER, LOWER], []))
        add(version, 'replace', REPLACE, executed([
            dict(id=1, x1=0, y1=20., x2=3, y2=23.), dict(id=2, x1=0, y1=5., x2=3, y2=7.)],
            [dict(id=2, line1=2, line2=1, color='#22446680')]))
        add(version, 'wrong-kind', 'linefill.delete(line.new(0, 10, 1, 5))\n', rejected('ARGUMENT_TYPE'))
    for version in range(1, 4):
        add(version, 'drawing-limits-version', 'plot(close)\n', rejected('DRAWING_LIMIT_VERSION'),
            options=', max_lines_count=2')
    add(4, 'polyline-limit-version', 'plot(close)\n', rejected('DRAWING_LIMIT_VERSION'),
        options=', max_polylines_count=1')
    for version in range(4, 7):
        body = CAPACITY_BASE
        options = ', max_lines_count=1+1, max_labels_count=1, max_boxes_count=2'
        expected = dict(status='executed', title='Channel', shorttitle='', overlay=True, observation_count=4,
                        outputs=[], qualifiers=[], labels=[dict(id=4, x=3, y=None, text='hidden')],
                        lines=[dict(id=2, x1=0, y1=5., x2=3, y2=5.),
                               dict(id=3, x1=0, y1=30., x2=3, y2=30.)],
                        boxes=[dict(id=3, left=2, top=30., right=3, bottom=0., border_color='#2196F3FF', bgcolor='#2196F3FF'),
                               dict(id=4, left=3, top=40., right=4, bottom=0., border_color='#2196F3FF', bgcolor='#2196F3FF')])
        if version >= 5:
            body += 'var linefill area = linefill.new(oldest, second, #33669980)\n'
            options += ', max_polylines_count=1'
            expected.update(tables=[], linefills=[], polylines=[dict(id=4, vertices=[[0, 12.], [3, 18.]],
                closed=False, xloc='bar_index', line_color='#2196F3FF', fill_color=None)])
        body += CAPACITY_BODY
        if version >= 5:
            body += CAPACITY_POLYLINE
        add(version, 'drawing-limits', body, expected, options=options)
    add(6, 'drawing-limit-bound', 'plot(close)\n', rejected('DRAWING_LIMIT_VALUE'),
        options=', max_polylines_count=101')
    for version in range(1, 5):
        add(version, 'drawing-copy-version', 'line.copy(na)\nplot(close)\n', rejected('DRAWING_COPY_VERSION'))
        add(version, 'drawing-inventory-version', 'ids = line.all\nplot(close)\n', rejected('DRAWING_ALL_VERSION'))
    for version in (5, 6):
        expected = executed([INVENTORY_FINAL], [])
        expected.update(outputs=[
            dict(title='lines', type='int', values=[2, 2, 1, 1]),
            dict(title='oldest', type='float', values=[20., 25., 30., 30.]),
            dict(title='fills', type='int', values=[1, 0, 0, 0]),
        ], qualifiers=['series'] * 3)
        add(version, 'drawing-inventory', INVENTORY, expected, options=', max_lines_count=2')
    add(6, 'drawing-inventory-read-only', 'array.clear(line.all)\nplot(close)\n',
        dict(status='rejected', category='PROFILE_UNCOVERED', code='DRAWING_ARRAY_READ_ONLY'))
    files['bars.json'] = (ROOT / 'conformance/boxes/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored line geometry, fill references, replacement, deletion and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/linefills'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Linefill fixtures verified.')


if __name__ == '__main__':
    main()
