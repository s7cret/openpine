"""Independent line projection, version, and mode expectations."""
import argparse

from packages.contracts.wire import canonical
from tools.lines_fixtures import source
from tools.specification import ROOT

MOVING = """var line ray = line.new(0, 10, 1, 20, extend=extend.right)
if bar_index == 1
    line.set_extend(ray, close < 20 ? extend.left : extend.none)
if bar_index >= 2
    line.set_extend(ray, bar_index == 2 ? extend.left : extend.both)
plot(line.get_price(ray, bar_index + 2))
"""

EDGE = """var line vertical = line.new(0, 10, 0, 20, extend=extend.both)
var line missing = line.new(0, na, 1, 20)
var line reverse = line.new(1, 20, 0, 10)
var line gone = line.new(0, 10, 1, 20)
line.delete(gone)
plot(line.get_price(vertical, 0))
plot(line.get_price(missing, 0))
plot(line.get_price(gone, 0))
plot(line.get_price(na, 0))
plot(line.get_price(reverse, -1))
plot(line.get_price(reverse, 2))
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

    def executed(lines, values):
        return dict(status='executed', title='Lines', shorttitle='', overlay=True, observation_count=4,
                    outputs=[dict(title='', type='float', values=series) for series in values],
                    qualifiers=['series'] * len(values), lines=lines, labels=[])

    for version in range(1, 4):
        add(version, 'getter-version', 'plot(line.get_price(na, 0))\n', rejected('LINE_VERSION'))
        add(version, 'setter-version', 'line.set_extend(na, extend.left)\n', rejected('LINE_VERSION'))
    for version in range(4, 7):
        add(version, 'moving', MOVING, executed(
            [dict(id=1, x1=0, y1=10., x2=1, y2=20., extend='both')], [[30., 40., 50., 60.]]))
        add(version, 'edges', EDGE, executed([
            dict(id=1, x1=0, y1=10., x2=0, y2=20., extend='both'),
            dict(id=2, x1=0, y1=None, x2=1, y2=20.),
            dict(id=3, x1=1, y1=20., x2=0, y2=10.)], [[None] * 4] * 4 + [[0.] * 4, [30.] * 4]))
        add(version, 'invalid-extend', 'line.new(0, 10, 1, 20, extend="bad")\n',
            rejected('LINE_EXTEND', 'RUNTIME_ERROR'))
    files['bars.json'] = (ROOT / 'conformance/lines/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-calculated 10 per bar slope, all modes and declared na boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/line-extensions'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Line extension fixtures verified.')


if __name__ == '__main__':
    main()
