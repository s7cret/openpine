"""Independent box border observations and version boundaries."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

TITLE = 'Box borders'
START = dict(id=1, left=0, top=30., right=1, bottom=10., xloc='bar_index',
             border_color='#E0112280', bgcolor='#33669980', border_width=2, border_style='solid', extend='none')
PREVIEW = {**START, 'border_width': 4, 'border_style': 'dotted', 'extend': 'both'}
CLOSED = {**START, 'border_width': 3, 'border_style': 'dashed', 'extend': 'right'}
FINAL_INDEX = {**CLOSED, 'bottom': 8.}
FINAL_TIME = {**FINAL_INDEX, 'right': 60000, 'xloc': 'bar_time'}
POINTS = dict(id=2, left=0, top=12., right=60000, bottom=4., xloc='bar_time',
              border_color='#556677FF', bgcolor=None, border_width=2, border_style='dotted', extend='left')
LEGACY = dict(id=1, left=0, top=30., right=1, bottom=10., border_color='#2196F3FF', bgcolor='#2196F3FF')


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}", overlay=true)\n{body}'


def appearance(version):
    body = """var box zone = box.new(0, 30, 1, 10, border_color=#E0112280, border_width=2, bgcolor=#33669980)
box alias = zone
"""
    if version >= 5:
        body += """var box points = box.new(chart.point.from_time(0, 12), chart.point.from_time(60000, 4), #556677, 2, line.style_dotted, extend.left, xloc.bar_time, na)
"""
    body += """if bar_index == 1
    box.set_border_width(alias, close < 20 ? 4 : 3)
    box.set_border_style(style=close < 20 ? line.style_dotted : line.style_dashed, id=alias)
    box.set_extend(alias, close < 20 ? extend.both : extend.right)
if bar_index == 2
    box.set_rightbottom(zone, 1, 8)
"""
    if version >= 6:
        body += '    box.set_xloc(zone, 0, 60000, xloc.bar_time)\n'
    return body


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        case = f'v{version}-{name}'
        file = case + '.pine'
        files[file] = source(version, body).encode()
        cases.append(dict(case_id=case, language_version=version, source=file, bars='bars.json', expected=expected))

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    def executed(boxes):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], labels=[], lines=[], boxes=boxes)

    for version in range(1, 4):
        add(version, 'constructor-version', 'box.new(0, 30, 1, 10, border_width=2)\n', rejected('BOX_VERSION'))
        for name, value in (('border_width', '2'), ('border_style', '"invalid"'), ('extend', '"invalid"')):
            add(version, name + '-setter-version', f'box.set_{name}(na, {value})\n', rejected('BOX_VERSION'))
    for version in (4, 5, 6):
        expected = [FINAL_TIME if version >= 6 else FINAL_INDEX] + ([POINTS] if version >= 5 else [])
        add(version, 'appearance', appearance(version), executed(expected))
        add(version, 'legacy-promotion', '''var box old = box.new(0, 30, 1, 10)
var box gone = box.new(0, 3, 1, 1)
if bar_index == 0
    box.delete(gone)
    box.set_extend(gone, "invalid")
    box.set_border_width(na, 0)
if bar_index == 1
    box.set_border_width(old, 4)
''', executed([dict(LEGACY, xloc='bar_index', border_width=4, border_style='solid', extend='none')]))
        add(version, 'coordinate-positional',
            'var box item = box.new(0, 30, 60000, 10, na, 64, line.style_dotted, extend.both, xloc.bar_time, #33669980)\n',
            executed([{**START, 'right': 60000, 'xloc': 'bar_time', 'border_color': None,
                       'border_width': 64, 'border_style': 'dotted', 'extend': 'both'}]))
        add(version, 'bad-width', 'box.new(0, 30, 1, 10, border_width=65)\n', rejected('BOX_BORDER_WIDTH', 'RUNTIME_ERROR'))
        add(version, 'bad-style', 'box.set_border_style(box.new(0, 30, 1, 10), line.style_arrow_left)\n',
            rejected('BOX_BORDER_STYLE', 'RUNTIME_ERROR'))
        add(version, 'bad-extend', 'box.set_extend(box.new(0, 30, 1, 10), "right")\n', rejected('BOX_EXTEND', 'RUNTIME_ERROR'))
    files['bars.json'] = (ROOT / 'conformance/drawing-xloc/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored raw box geometry, border settings and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/box-borders'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Box border fixtures verified.')


if __name__ == '__main__':
    main()
