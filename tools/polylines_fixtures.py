"""Manual expectations for point arrays and immutable polyline contours."""
import argparse

from packages.contracts.wire import canonical
from tools.boxes_fixtures import STRATEGY_ZONE, strategy_zone_source
from tools.linefills_fixtures import strategy_channel_expected
from tools.specification import ROOT

DRAWINGS = """var chart.point seed = chart.point.now()
var array<chart.point> points = array.new<chart.point>()
if bar_index == 0
    array.push(points, seed)
    array.push(points, chart.point.from_index(3, 40))
var polyline first = polyline.new(points, line_color=#00FF00)
var polyline current = na
if bar_index == 1
    alias = array.get(points, 0)
    alias.price := close + 10
    array.set(points, 1, chart.point.from_index(3, 45))
    current := polyline.new(points)
if bar_index == 2
    polyline.delete(current)
    array.clear(points)
    corners = array.from(chart.point.from_time(0, 12), chart.point.from_time(150000, 42), chart.point.from_time(300000, 15))
    current := polyline.new(corners, curved=false, closed=true, xloc=xloc.bar_time, line_color=na, fill_color=#33669980)
plot(seed.price, "point")
plot(array.size(points), "vertices")
"""
ARRAYS = """var array<chart.point> points = array.from(chart.point.now(), na)
array.set(points, 1, chart.point.now(close + 3))
alias = array.get(points, 0)
alias.price := alias.price + 1
first = array.get(points, 0)
second = array.get(points, 1)
plot(first.price, "retained")
plot(second.price, "current")
"""
FIRST = dict(id=1, vertices=[[0, 10.], [3, 40.]], closed=False, xloc='bar_index', line_color='#00FF00FF', fill_color=None)
SECOND = dict(id=2, vertices=[[0, 30.], [3, 45.]], closed=False, xloc='bar_index', line_color='#2196F3FF', fill_color=None)
POLYGON = dict(id=3, vertices=[[0, 12.], [150000, 42.], [300000, 15.]], closed=True, xloc='bar_time',
               line_color=None, fill_color='#33669980')
SERIES = ([10., 30., 30., 30.], [2, 2, 0, 0])


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("Polylines", overlay=true)\n{body}'


def strategy_region_source(version, *, on_tick=False, evict=False):
    text = strategy_zone_source(version, channel=True, on_tick=on_tick, evict=evict)
    if evict:
        text = text.replace('overlay=true', 'overlay=true, max_polylines_count=1', 1)
    text += '''var array<chart.point> vertices = array.from(chart.point.from_time(0, 12), chart.point.from_time(150000, 42), chart.point.from_time(300000, 15))
var polyline region = polyline.new(vertices, closed=true, xloc=xloc.bar_time, line_color=na, fill_color=#33669980)
'''
    if evict:
        text += '''if bar_index == 2
    array.set(vertices, 1, chart.point.from_time(time, high))
    polyline.new(vertices, closed=true, xloc=xloc.bar_time, line_color=na, fill_color=#AA222280)
if bar_index == 3
    region.delete()
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

    def executed(outputs, **fields):
        return dict(status='executed', title='Polylines', shorttitle='', overlay=True, observation_count=4,
                    outputs=[dict(title=title, type=kind, values=values) for title, kind, values in outputs],
                    qualifiers=['series'] * len(outputs), **fields)

    pair = 'array.from(chart.point.from_index(0, 2), chart.point.from_index(3, 4))'
    for v in range(1, 5):
        add(v, 'type-version', 'polyline path = na\nplot(1)\n', rejected('POLYLINE_VERSION'))
        add(v, 'call-version', 'polyline.new(na)\n', rejected('POLYLINE_VERSION'))
        add(v, 'force-overlay-version', 'polyline.new(na, force_overlay=true)\n', rejected('POLYLINE_VERSION'))
        add_case(v, 'strategy-region', f'//@version={v}\nstrategy("Limit levels")\npolyline.new(na)\n',
                 rejected('POLYLINE_VERSION'))
    for v in (5, 6):
        expected = {**strategy_channel_expected(), 'boxes': [STRATEGY_ZONE], 'polylines': [{**POLYGON, 'id': 1}]}
        add_case(v, 'strategy-region', strategy_region_source(v), expected)
        add(v, 'drawings', DRAWINGS, executed([('point', 'float', SERIES[0]), ('vertices', 'int', SERIES[1])],
            labels=[], lines=[], boxes=[], tables=[], linefills=[], polylines=[FIRST, POLYGON]))
        add(v, 'force-overlay-time',
            'var array<chart.point> corners = array.from(chart.point.from_time(0, 12), chart.point.from_time(150000, 42), chart.point.from_time(300000, 15))\n'
            'var polyline region = polyline.new(corners, closed=true, xloc=xloc.bar_time, line_color=na, fill_color=#33669980, force_overlay=true)\n',
            executed([], labels=[], lines=[], boxes=[], tables=[], linefills=[],
                     polylines=[{**POLYGON, 'id': 1, 'force_overlay': True}]))
        add(v, 'arrays', ARRAYS, executed([('retained', 'float', [11., 12., 13., 14.]),
                                          ('current', 'float', [13., 23., 33., 43.])]))
        add(v, 'curved', f'polyline.new({pair}, curved=true)\n', rejected('POLYLINE_CURVED', 'PROFILE_UNCOVERED'))
        add(v, 'closed-minimum', f'polyline.new({pair}, closed=true)\n', rejected('POLYLINE_VERTICES', 'RUNTIME_ERROR'))
        add(v, 'missing-time', f'polyline.new({pair}, xloc=xloc.bar_time)\n', rejected('POLYLINE_VALUE', 'RUNTIME_ERROR'))
        add(v, 'missing-point', 'polyline.new(array.new<chart.point>(2))\n', rejected('POLYLINE_VALUE', 'RUNTIME_ERROR'))
        add(v, 'vertex-limit', 'polyline.new(array.new<chart.point>(513, chart.point.now()))\n',
            rejected('POLYLINE_VERTICES', 'RUNTIME_ERROR'))
        add(v, 'wrong-array', 'polyline.new(array.from(1, 2))\n', rejected('ARGUMENT_TYPE'))
        add(v, 'wrong-mode', f'polyline.new({pair}, xloc="bar_time")\n', rejected('DRAWING_XLOC'))
        add(v, 'force-overlay-series', f'polyline.new({pair}, force_overlay=close > 0)\n', rejected('ARGUMENT_QUALIFIER'))
        add(v, 'force-overlay-type', f'polyline.new({pair}, force_overlay=1)\n', rejected('ARGUMENT_TYPE'))
        add(v, 'force-overlay-positional', f'polyline.new({pair}, false, false, xloc.bar_index, na, na, true)\n',
            rejected('ARGUMENT_TYPE'))
        stroke = dict(id=1, vertices=[[0, 2.], [3, 4.]], closed=False, xloc='bar_index',
                      line_color='#2196F3FF', fill_color=None)
        add(v, 'styled-positional', f'var polyline path = polyline.new({pair}, false, false, xloc.bar_index, #2196F3, na, line.style_arrow_both, 3, true)\n',
            executed([], labels=[], lines=[], boxes=[], tables=[], linefills=[], polylines=[{**stroke, 'line_style': 'arrow_both', 'line_width': 3, 'force_overlay': True}]))
        add(v, 'styled-width', f'var polyline path = polyline.new({pair}, line_width=2, force_overlay=false)\n',
            executed([], labels=[], lines=[], boxes=[], tables=[], linefills=[], polylines=[{**stroke, 'line_style': 'solid', 'line_width': 2}]))
        add(v, 'styled-width-range', f'polyline.new({pair}, line_width=65)\n', rejected('LINE_WIDTH', 'RUNTIME_ERROR'))
        add(v, 'point-array-pop', f'points = {pair}\nplot(array.pop(points).price)\n',
            rejected('POINT_ARRAY_OPERATION', 'PROFILE_UNCOVERED'))

    files['bars.json'] = (ROOT / 'conformance/chart-points/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Fixed bars, ordered vertex snapshots, array aliases, coordinate modes and bounded geometry.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/polylines'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Polyline fixtures verified.')


if __name__ == '__main__':
    main()
