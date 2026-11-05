"""Independent expectations for drawing coordinates before and after xloc changes."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

TITLE = 'Drawing coordinate changes'
INDEX_LINE = dict(id=1, x1=0, y1=25., x2=3, y2=40.)
INDEX_LABEL = dict(id=1, x=0, y=35., text='gap')
INDEX_BOX = dict(id=1, left=0, top=52., right=3, bottom=3., border_color='#FF0000FF', bgcolor='#12345680')
TIME_LINE = {**INDEX_LINE, 'x1': 150000, 'x2': 300000, 'xloc': 'bar_time', 'extend': 'none'}
TIME_LABEL = {**INDEX_LABEL, 'x': 150000, 'xloc': 'bar_time'}
TIME_BOX = {**INDEX_BOX, 'left': 150000, 'right': 300000, 'xloc': 'bar_time'}
FLOOR = dict(id=2, x1=0, y1=10., x2=3, y2=12., extend='right')
FILL = dict(id=1, line1=1, line2=2, color='#33669980')
PLOTS = ('line x1', 'line x2', 'label x', 'box left', 'box right')
TIMED = ([0, 150000, 150000, 150000], [3, 300000, 300000, 300000])


def series(version):
    return (*TIMED, TIMED[0], *(TIMED if version >= 6 else ([0] * 4, [3] * 4)))


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}", overlay=true)\n{body}'


def drawings(version):
    body = """var line roof = line.new(0, 25, 3, 40)
var label tag = label.new(0, 35, "gap")
var box zone = box.new(0, 52, 3, 3, border_color=#FF0000, bgcolor=#12345680)
line roof_alias = roof
label tag_alias = tag
box zone_alias = zone
"""
    if version >= 5:
        body += """var line floor = line.new(0, 10, 3, 12, extend=extend.right)
var linefill fill_id = linefill.new(roof, floor, #33669980)
"""
    body += """if bar_index == 1
    int right = close < 20 ? 240000 : 300000
    line.set_xloc(roof_alias, time + 90000, right, xloc.bar_time)
    label.set_xloc(xloc=xloc.bar_time, x=time + 90000, id=tag_alias)
"""
    if version >= 6:
        body += '    box.set_xloc(zone_alias, time + 90000, right, xloc.bar_time)\n'
    body += """if bar_index == 2 and close < 30
    line.set_xloc(roof_alias, -1, 3, xloc.bar_index)
    label.set_xloc(tag_alias, -1, xloc.bar_index)
"""
    if version >= 6:
        body += '    box.set_xloc(zone_alias, -1, 3, xloc.bar_index)\n'
    return body + """plot(line.get_x1(id=roof), "line x1")
plot(line.get_x2(roof_alias), "line x2")
plot(label.get_x(tag), "label x")
plot(box.get_left(zone), "box left")
plot(box.get_right(zone_alias), "box right")
"""


PRICE_TIME = """var line roof = line.new(0, 10, 2, 12, extend=extend.both)
line.set_xloc(roof, time, time + 60000, xloc.bar_time)
plot(line.get_price(roof, bar_index), "price")
"""
PRICE_INDEX = """var line roof = line.new(0, 10, 2, 12, extend=extend.both)
if bar_index == 1
    line.set_xloc(roof, time, time + 60000, xloc.bar_time)
if bar_index == 2
    line.set_xloc(roof, -1, 1, xloc.bar_index)
float price = na
if bar_index != 1
    price := line.get_price(roof, bar_index)
plot(price, "price")
plot(line.get_x1(roof), "line x1")
"""
MISSING = """var line roof = line.new(na, 25, 3, 40)
var label tag = label.new(na, 35, "gap")
var box zone = box.new(na, 52, 3, 3)
if bar_index == 1
    line.delete(roof)
    label.delete(tag)
    box.delete(zone)
    line.set_xloc(roof, 0, 1, xloc.bar_index)
    label.set_xloc(tag, 0, xloc.bar_index)
plot(line.get_x1(roof), "missing line x")
plot(line.get_x2(roof), "line x after delete")
plot(label.get_x(tag), "missing label x")
plot(box.get_left(zone), "missing box x")
plot(box.get_right(zone), "box x after delete")
plot(line.get_x2(na), "missing line id")
plot(label.get_x(na), "missing label id")
plot(box.get_right(na), "missing box id")
"""


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        case_id = f'v{version}-{name}'
        filename = case_id + '.pine'
        files[filename] = source(version, body).encode()
        cases.append(dict(case_id=case_id, language_version=version, source=filename,
                          bars='bars.json', expected=expected))

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    def executed(outputs, **records):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=True, observation_count=4,
                    outputs=[dict(title=title, type=base, values=values) for title, base, values in outputs],
                    qualifiers=['series'] * len(outputs), **records)

    for version in range(1, 4):
        for kind, coordinates, getter in (('line', '0, 1', 'get_x1'), ('label', '0', 'get_x'), ('box', '0, 1', 'get_left')):
            diagnostic = rejected(kind.upper() + '_VERSION')
            add(version, kind + '-setter-version', f'{kind}.set_xloc(na, {coordinates}, xloc.bar_index)\n', diagnostic)
            add(version, kind + '-getter-version', f'plot({kind}.{getter}(na))\n', diagnostic)
    missing = ('missing line x', 'line x after delete', 'missing label x', 'missing box x',
               'box x after delete', 'missing line id', 'missing label id', 'missing box id')
    for version in (4, 5, 6):
        records = dict(labels=[TIME_LABEL], lines=[TIME_LINE], boxes=[TIME_BOX if version >= 6 else INDEX_BOX])
        if version >= 5:
            records['lines'].append(FLOOR)
            records.update(tables=[], linefills=[FILL])
        add(version, 'switch', drawings(version), executed(
            [(title, 'int', values) for title, values in zip(PLOTS, series(version), strict=True)], **records))
        add(version, 'price-time', PRICE_TIME, rejected('LINE_PRICE_XLOC', 'RUNTIME_ERROR'))
        add(version, 'price-index', PRICE_INDEX, executed(
            [('price', 'float', [10., None, 13., 14.]), ('line x1', 'int', [0, 60000, -1, -1])],
            labels=[], lines=[dict(id=1, x1=-1, y1=10., x2=1, y2=12., xloc='bar_index', extend='both')]))
        add(version, 'missing', MISSING, executed(
            [(title, 'int', [3, None, None, None] if i in (1, 4) else [None] * 4) for i, title in enumerate(missing)],
            labels=[], lines=[], boxes=[]))
        add(version, 'bad-mode', 'label.set_xloc(na, 0, "xloc.bar_time")\n', rejected('DRAWING_XLOC'))
        if version < 6:
            add(version, 'box-setter-version', 'box.set_xloc(na, 0, 1, xloc.bar_index)\n', rejected('BOX_SET_XLOC_VERSION'))
    files['bars.json'] = (ROOT / 'conformance/drawing-xloc/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored raw coordinates, drawing state and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/drawing-xloc-mutations'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Drawing coordinate mutation and read fixtures verified.')


if __name__ == '__main__':
    main()
