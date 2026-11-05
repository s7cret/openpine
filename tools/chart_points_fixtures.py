"""Hand-authored chart point expectations and version boundaries."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

DRAWINGS = """var chart.point seed = chart.point.now()
alias = seed
var chart.point copied = chart.point.copy(seed)
var line first = line.new(seed, chart.point.from_index(2, 12), extend=extend.right)
if bar_index == 0
    alias.price := 16
var line second = line.new(second_point=chart.point.new(300000, 3, 35), first_point=seed, xloc=xloc.bar_time, extend=extend.right)
var label tag = label.new(seed, "point", xloc=xloc.bar_time)
var box zone = box.new(top_left=seed, bottom_right=chart.point.from_time(300000, 5), xloc=xloc.bar_time, bgcolor=#12345680)
var linefill channel = linefill.new(second, first, #33669980)
if bar_index == 1
    alias.price := close + 10
    line.set_first_point(second, chart.point.now(alias.price))
    label.set_point(tag, chart.point.from_time(time + 90000, alias.price))
    box.set_top_left_point(zone, chart.point.from_time(time + 90000, alias.price + 10))
if bar_index == 2
    line.set_second_point(second, chart.point.new(300000, 99, 45))
    box.set_bottom_right_point(zone, chart.point.from_time(time + 120000, 3))
plot(seed.price, "alias")
plot(copied.price, "copy")
"""
FIELDS = """here = chart.point.now()
clone = here.copy()
by_time = chart.point.from_time(here.time, here.price)
by_index = chart.point.from_index(here.index, here.price)
here.price := here.price + 1
plot(here.time, "time")
plot(here.index, "index")
plot(here.price, "price")
plot(clone.price, "copy")
plot(by_time.index, "missing index")
plot(by_index.time, "missing time")
"""
MISSING_AXIS = """var label hidden = label.new(chart.point.from_time(0, 20))
var line partial = line.new(chart.point.from_index(0, 10), chart.point.from_time(60000, 15), xloc=xloc.bar_time)
"""

FIRST = dict(id=1, x1=0, y1=10., x2=2, y2=12., xloc='bar_index', extend='right')
SECOND = dict(id=2, x1=60000, y1=30., x2=300000, y2=45., xloc='bar_time', extend='right')
LABEL = dict(id=1, x=150000, y=30., text='point', xloc='bar_time')
BOX = dict(id=1, left=150000, top=40., right=360000, bottom=3., xloc='bar_time',
           border_color='#2196F3FF', bgcolor='#12345680')
FILL = dict(id=1, line1=2, line2=1, color='#33669980')
SERIES = ([16., 30., 30., 30.], [10., 10., 10., 10.])


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Chart points", overlay=true)\n{body}'


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

    def executed(outputs=(), **fields):
        return dict(status='executed', title='Chart points', shorttitle='', overlay=True, observation_count=4,
                    outputs=[dict(title=title, type=kind, values=values) for title, kind, values in outputs],
                    qualifiers=['series'] * len(outputs), **fields)

    for version in range(1, 5):
        add(version, 'point-type-version', 'chart.point row = na\nplot(1)\n', rejected('POINT_VERSION'))
        add(version, 'point-call-version', 'row = chart.point.now()\nplot(1)\n', rejected('POINT_VERSION'))
    for version in (5, 6):
        add(version, 'drawings', DRAWINGS, executed(
            [('alias', 'float', SERIES[0]), ('copy', 'float', SERIES[1])],
            labels=[LABEL], lines=[FIRST, SECOND], boxes=[BOX], tables=[], linefills=[FILL]))
        add(version, 'fields', FIELDS, executed([
            ('time', 'int', [0, 60000, 240000, 300000]), ('index', 'int', [0, 1, 2, 3]),
            ('price', 'float', [11., 21., 31., 41.]), ('copy', 'float', [10., 20., 30., 40.]),
            ('missing index', 'int', [None] * 4), ('missing time', 'int', [None] * 4)]))
        add(version, 'missing-axis', MISSING_AXIS, executed(
            labels=[dict(id=1, x=None, y=20., text='', xloc='bar_index')],
            lines=[dict(id=1, x1=None, y1=10., x2=60000, y2=15., xloc='bar_time', extend='none')]))
        add(version, 'wrong-mode', 'label.new(chart.point.now(), xloc="bar_time")\n', rejected('DRAWING_XLOC'))
        add(version, 'wrong-type', 'row = chart.point.from_time("time", 10)\nplot(1)\n', rejected('ARGUMENT_TYPE'))
        add(version, 'write-time', 'row = chart.point.now()\nrow.time := 0\nplot(1)\n',
            rejected('POINT_FIELD_WRITE', 'PROFILE_UNCOVERED'))
        add(version, 'missing-reference', 'chart.point row = na\nlabel.new(row)\n', rejected('UDT_NA', 'RUNTIME_ERROR'))

    files['bars.json'] = (ROOT / 'conformance/drawing-xloc/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Fixed bar times, alias and copy observations, point field selection and drawing snapshots.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/chart-points'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Chart point fixtures verified.')


if __name__ == '__main__':
    main()
