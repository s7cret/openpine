"""Hand-authored timestamp drawing and version-boundary observations."""
import argparse

from packages.contracts.wire import canonical, read_json
from tools.specification import ROOT

DRAWINGS = """var line roof = line.new(time, 25, time + 300000, 40, xloc=xloc.bar_time, extend=extend.right)
var label tag = label.new(time, 30, "gap", xloc=xloc.bar_time)
var box zone = box.new(time, 50, time + 300000, 5, xloc=xloc.bar_time, bgcolor=#12345680)
if bar_index == 1
    line.set_xy1(roof, time, close < 20 ? 24 : 28)
    label.set_xy(tag, time + 90000, 35)
    box.set_lefttop(zone, time + 90000, 52)
if bar_index == 2
    box.set_rightbottom(zone, time + 120000, 3)
"""
MIXED = DRAWINGS + """var line floor = line.new(0, 10, 2, 12, xloc=xloc.bar_index, extend=extend.right)
var linefill channel = linefill.new(roof, floor, #33669980)
if bar_index == 1
    linefill.set_color(channel, close < 20 ? #FF000080 : #33669980)
"""
TIMES = (0, 60000, 240000, 300000)
LINE = dict(id=1, x1=60000, y1=28., x2=300000, y2=40., xloc='bar_time', extend='right')
LABEL = dict(id=1, x=150000, y=35., text='gap', xloc='bar_time')
BOX = dict(id=1, left=150000, top=52., right=360000, bottom=3., xloc='bar_time',
           border_color='#2196F3FF', bgcolor='#12345680')
FLOOR = dict(id=2, x1=0, y1=10., x2=2, y2=12., xloc='bar_index', extend='right')
FILL = dict(id=1, line1=1, line2=2, color='#33669980')


def source(version, body=DRAWINGS, *, overlay=True):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Time drawings", overlay={str(overlay).lower()})\n{body}'


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

    def executed(**fields):
        return dict(status='executed', title='Time drawings', shorttitle='', overlay=True,
                    observation_count=4, **{'outputs': [], 'qualifiers': [], **fields})

    for v in range(1, 4):
        for kind, coordinates in (('line', '0, 1, 2, 3'), ('label', '0, 1'), ('box', '0, 3, 2, 1')):
            add(v, kind + '-version', f'{kind}.new({coordinates}, xloc=xloc.bar_time)\n',
                rejected(kind.upper() + '_VERSION'))
    for v in (4, 5, 6):
        add(v, 'drawings', DRAWINGS, executed(lines=[LINE], labels=[LABEL], boxes=[BOX]))
        add(v, 'get-price-time', 'line row = line.new(time, 1, time+60000, 2, xloc=xloc.bar_time)\n'
            'plot(line.get_price(row, bar_index))\n', rejected('LINE_PRICE_XLOC', 'RUNTIME_ERROR'))
        add(v, 'get-price-index', 'var line row = line.new(0, 10, 2, 12, xloc=xloc.bar_index)\n'
            'plot(line.get_price(row, bar_index), "index")\n', executed(labels=[],
                lines=[{**FLOOR, 'id': 1, 'extend': 'none'}],
                outputs=[dict(title='index', type='float', values=[10., 11., 12., 13.])], qualifiers=['series']))
        add(v, 'bad-mode', 'label.new(0, 2, xloc="bar_time")\n', rejected('DRAWING_XLOC'))
        if v >= 5:
            add(v, 'mixed', MIXED, executed(lines=[LINE, FLOOR], labels=[LABEL], boxes=[BOX],
                                           tables=[], linefills=[FILL]))
    data = read_json(ROOT / 'conformance/boxes/bars.json')
    for bar, time in zip(data['bars'], TIMES, strict=True):
        bar['time'] = time
    files['bars.json'] = canonical(data) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored timestamps, unchanged indices, preserved modes and rejection expectations.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/drawing-xloc'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Drawing coordinate fixtures verified.')


if __name__ == '__main__':
    main()
