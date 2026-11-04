"""Independent expectations for coordinate boxes and version boundaries."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

MOVING = """var box zone = box.new(na, na, na, na, bgcolor=#12345680)
var line guide = line.new(0, 10, 3, 40)
var label tag = label.new(na, na, "zone")
alias = zone
box.set_lefttop(alias, 0, 50)
box.set_rightbottom(alias, bar_index + 1, 5)
label.set_xy(tag, bar_index, close)
if bar_index == 1
    box.set_bgcolor(zone, close < 20 ? #FF000080 : na)
    box.set_border_color(zone, #112233FF)
if bar_index == 2
    box.set_bgcolor(zone, #33669980)
"""


def source(version, body=MOVING):
    declaration = 'indicator' if version >= 5 else 'study'
    return f'//@version={version}\n{declaration}("Boxes", overlay=true)\n{body}'


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

    def executed(boxes, lines=(), labels=()):
        return dict(status='executed', title='Boxes', shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], boxes=boxes, lines=list(lines), labels=list(labels))

    for version in range(1, 4):
        add(version, 'call-version', 'box.new(0, 10, 1, 5)\n', rejected('BOX_VERSION'))
        add(version, 'type-version', 'var box item = na\nplot(close)\n', rejected('BOX_VERSION'))
    for version in range(4, 7):
        add(version, 'moving', MOVING, executed(
            [dict(id=1, left=0, top=50., right=4, bottom=5., border_color='#112233FF', bgcolor='#33669980')],
            [dict(id=1, x1=0, y1=10., x2=3, y2=40.)], [dict(id=1, x=3, y=40., text='zone')]))
        add(version, 'defaults', '''var box item = box.new(3, 5, 0, 10)
var box hidden = box.new(na, na, 0, 5)
var box gone = box.new(0, 10, 1, 5)
box.delete(gone)
box.set_bgcolor(gone, na)
box.delete(na)
''', executed([dict(id=1, left=3, top=5., right=0, bottom=10., border_color='#2196F3FF', bgcolor='#2196F3FF'),
               dict(id=2, left=None, top=None, right=0, bottom=5., border_color='#2196F3FF', bgcolor='#2196F3FF')]))
        add(version, 'wrong-kind', 'box.delete(line.new(0, 10, 1, 5))\n', rejected('ARGUMENT_TYPE'))
        add(version, 'color-position', 'box.new(0, 10, 1, 5, color.blue)\n',
            rejected('BOX_POSITIONAL_PROFILE', 'PROFILE_UNCOVERED'))
        add(version, 'time-coordinate', 'box.new(0, 10, 1, 5, xloc="bar_time")\n', rejected('DRAWING_XLOC'))
    files['bars.json'] = (ROOT / 'conformance/lines/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored coordinates, RGBA values, drawing identities and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/boxes'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Box fixtures verified.')


if __name__ == '__main__':
    main()
