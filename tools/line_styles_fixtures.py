"""Independent observations for styled lines and version boundaries."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

TITLE = 'Line appearance'
START = dict(id=1, x1=0, y1=10., x2=2, y2=20., xloc='bar_index', extend='none',
             color='#EE110080', style='dashed', width=2)
PREVIEW = {**START, 'color': '#44AA22FF', 'style': 'dotted', 'width': 4}
CLOSED = {**START, 'color': None, 'style': 'arrow_left', 'width': 3}
FINAL = {**CLOSED, 'x1': 150000, 'x2': 300000, 'xloc': 'bar_time', 'extend': 'both'}
FLOOR = dict(id=2, x1=0, y1=5., x2=3, y2=5., xloc='bar_index', extend='none',
             color='#2196F3FF', style='solid', width=2)
POINTS = dict(id=3, x1=0, y1=7., x2=300000, y2=8., xloc='bar_time', extend='none',
              color='#44556680', style='arrow_both', width=2)
FILL = dict(id=1, line1=1, line2=2, color='#33669980')
PRICES = [10., 15., None, None]
OVERLAY_LINES = [
    {**START, 'x2': 3, 'y2': 10., 'force_overlay': True},
    {**FLOOR, 'force_overlay': True},
    {**FLOOR, 'id': 3, 'y1': 30., 'y2': 30., 'width': 1},
    dict(id=4, x1=0, y1=22., x2=3, y2=22.),
]
OVERLAY_FILLS = [FILL, dict(id=2, line1=3, line2=4, color='#AABBCCFF'),
                 dict(id=3, line1=1, line2=3, color='#FF0000FF')]
OVERLAY_POLYLINES = [
    dict(id=1, vertices=[[0, 10.], [1, 16.], [3, 12.]], closed=True, xloc='bar_index',
         line_color='#2196F3FF', fill_color='#33669980', force_overlay=True),
    dict(id=2, vertices=[[0, 10.], [1, 16.], [3, 12.]], closed=False, xloc='bar_index',
         line_color='#2196F3FF', fill_color=None),
]
OVERLAY = """var line roof = line.new(0, 10, 3, 10, xloc.bar_index, extend.none, #EE110080, line.style_dashed, 2, true)
var line floor = line.new(chart.point.from_index(0, 5), chart.point.from_index(3, 5), width=2, force_overlay=true)
var line upper = line.new(0, 30, 3, 30, force_overlay=false)
var line lower = line.new(0, 22, 3, 22)
var linefill priceFill = linefill.new(roof, floor, #33669980)
var linefill paneFill = linefill.new(upper, lower, #AABBCC)
var linefill mixed = linefill.new(roof, upper, #FF0000)
var array<chart.point> vertices = array.from(chart.point.from_index(0, 10), chart.point.from_index(1, 16), chart.point.from_index(3, 12))
var polyline region = polyline.new(vertices, closed=true, fill_color=#33669980, force_overlay=true)
var polyline path = na
if bar_index == 1
    line.set_width(roof, close < 20 ? 4 : 3)
    path := polyline.new(vertices, force_overlay=false)
    linefill.set_color(mixed, close < 20 ? #00FF00 : #FF0000)
plot(line.get_price(linefill.get_line2(mixed), bar_index), "mixed endpoint")
"""


def source(version, body, *, overlay=True):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}", overlay={str(overlay).lower()})\n{body}'


def appearance(version):
    body = """var line roof = line.new(0, 10, 2, 20, color=#EE110080, style=line.style_dashed, width=2)
var line floor = line.new(0, 5, 3, 5, width=2)
line alias = roof
"""
    if version >= 5:
        body += """var line dots = line.new(chart.point.from_time(0, 7), chart.point.from_time(300000, 8), xloc.bar_time, extend.none, #44556680, line.style_arrow_both, 2)
var linefill channel = linefill.new(roof, floor, #33669980)
"""
    return body + """if bar_index == 1
    line.set_color(alias, close < 20 ? #44AA22 : color(na))
    line.set_style(id=alias, style=close < 20 ? line.style_dotted : line.style_arrow_left)
    line.set_width(alias, close < 20 ? 4 : 3)
float price = na
if bar_index < 2
    price := line.get_price(roof, bar_index)
if bar_index == 2
    line.set_xloc(roof, 150000, 300000, xloc.bar_time)
    line.set_extend(roof, extend.both)
plot(price, "price")
"""


LEGACY = """var line original = line.new(0, 10, 2, 20)
var line gone = line.new(0, 0, 1, 1)
if bar_index == 0
    line.delete(gone)
    line.set_style(gone, "invalid")
    line.set_width(na, 0)
if bar_index == 1
    line.set_style(original, line.style_arrow_right)
"""


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected, *, overlay=True):
        case = f'v{version}-{name}'
        file = case + '.pine'
        files[file] = source(version, body, overlay=overlay).encode()
        cases.append(dict(case_id=case, language_version=version, source=file, bars='bars.json', expected=expected))

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    def executed(lines, outputs=(), **records):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=True, observation_count=4,
                    outputs=list(outputs), qualifiers=['series'] * len(outputs), labels=[], lines=lines, **records)

    for version in range(1, 4):
        add(version, 'constructor-version', 'line.new(0, 10, 2, 20, width=2)\n', rejected('LINE_VERSION'))
        for name, value in (('color', 'na'), ('style', '"invalid"'), ('width', '2')):
            add(version, name + '-setter-version', f'line.set_{name}(na, {value})\n', rejected('LINE_VERSION'))
    for version in (4, 5, 6):
        expected = [FINAL, FLOOR, POINTS] if version >= 5 else [FINAL, FLOOR]
        records = dict(boxes=[], tables=[], linefills=[FILL]) if version >= 5 else {}
        add(version, 'appearance', appearance(version),
            executed(expected, [dict(title='price', type='float', values=PRICES)], **records))
        legacy = {**START, 'color': '#2196F3FF', 'style': 'arrow_right', 'width': 1}
        add(version, 'legacy-promotion', LEGACY, executed([legacy]))
        add(version, 'coordinate-positional',
            'line.new(0, 10, 2, 20, xloc.bar_index, extend.left, na, line.style_solid, 64)\n',
            executed([{**START, 'id': identity, 'extend': 'left', 'color': None, 'style': 'solid', 'width': 64}
                      for identity in range(1, 5)]))
        add(version, 'bad-width', 'line.new(0, 10, 2, 20, width=65)\n', rejected('LINE_WIDTH', 'RUNTIME_ERROR'))
        add(version, 'missing-width', 'line.set_width(line.new(0, 10, 2, 20), na)\n', rejected('LINE_WIDTH', 'RUNTIME_ERROR'))
        add(version, 'bad-style', 'line.set_style(line.new(0, 10, 2, 20), "dashed")\n', rejected('LINE_STYLE', 'RUNTIME_ERROR'))
    for version in range(1, 5):
        add(version, 'force-overlay-version', 'line.new(0, 1, 2, 3, force_overlay=true)\n',
            rejected('LINE_OVERLAY_VERSION' if version == 4 else 'LINE_VERSION'))
    for version in (5, 6):
        final = [{**OVERLAY_LINES[0], 'width': 3}, *OVERLAY_LINES[1:]]
        outputs = [dict(title='mixed endpoint', type='float', values=[30.] * 4)]
        expected = executed(final, outputs, boxes=[], tables=[], linefills=OVERLAY_FILLS, polylines=OVERLAY_POLYLINES)
        add(version, 'force-overlay-mixed', OVERLAY, {**expected, 'overlay': False}, overlay=False)
        add(version, 'force-overlay-series', 'line.new(0, 1, 2, 3, force_overlay=close > 0)\n', rejected('ARGUMENT_QUALIFIER'))
        add(version, 'force-overlay-type', 'line.new(0, 1, 2, 3, force_overlay=1)\n', rejected('ARGUMENT_TYPE'))
        add(version, 'force-overlay-point-positional',
            'var line path = line.new(chart.point.from_index(0, 5), chart.point.from_index(3, 5), xloc.bar_index, '
            'extend.none, color.blue, line.style_solid, 2, true)\n', executed([OVERLAY_LINES[1] | {'id': 1}]))
    files['bars.json'] = (ROOT / 'conformance/drawing-xloc/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored geometry, colors, line styles, output prices and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/line-styles'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Line styles fixtures verified.')


if __name__ == '__main__':
    main()
