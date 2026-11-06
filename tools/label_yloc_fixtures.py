"""Independent expectations for price and candle-relative label coordinates."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

TITLE = 'Label vertical coordinates'
START = dict(id=1, x=0, y=10., text='moving', xloc='bar_index',
             color='#12345680', textcolor='#FFFFFFFF', style='down', size='small')
BELOW = dict(id=2, x=1, y=None, text='bar', xloc='bar_index', yloc='belowbar',
             color='#2196F3FF', textcolor='#FFFFFFFF', style='up', size='normal')
GAP = dict(BELOW, id=3, x=150000, text='gap', xloc='bar_time', yloc='abovebar', style='down')
POINT = dict(GAP, id=4, x=60000, text='point')
CLOSED = dict(START, x=60000, y=25., xloc='bar_time', yloc='abovebar')
FINAL = dict(CLOSED, yloc='price', text='restored')


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}", overlay=true)\n{body}'


def labels(version):
    return [BELOW, GAP] + ([POINT] if version >= 5 else [])


def vertical_program(version):
    body = """var label tag = label.new(0, 10, "moving", color=#12345680, size=size.small)
label alias = tag
var label candle = label.new(1, na, "bar", yloc=close > 0 ? yloc.belowbar : yloc.abovebar, style=label.style_label_up)
var label gap = label.new(150000, na, "gap", xloc.bar_time, yloc.abovebar)
"""
    if version >= 5:
        body += 'var label exact = label.new(chart.point.from_time(60000, na), "point", xloc.bar_time, yloc.abovebar)\n'
    return body + """if bar_index == 1
    label.set_yloc(yloc=close < 20 ? yloc.belowbar : yloc.abovebar, id=alias)
    label.set_y(alias, close + 5)
    label.set_xloc(alias, 60000, xloc.bar_time)
if bar_index == 2
    label.set_yloc(tag, yloc.price)
    label.set_text(alias, "restored")
"""


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        case = f'v{version}-{name}'
        file = case + '.pine'
        files[file] = source(version, body).encode()
        cases.append(dict(case_id=case, language_version=version, source=file, bars='bars.json', expected=expected))

    def executed(rows):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], labels=rows)

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 4):
        for name, body in (('constructor', 'label.new(1, na, yloc=yloc.abovebar)'),
                           ('mode-setter', 'label.set_yloc(na, yloc.belowbar)'), ('price-setter', 'label.set_y(na, 12)')):
            add(version, name + '-version', body + '\n', rejected('LABEL_VERSION'))
    for version in (4, 5, 6):
        add(version, 'bar-anchored', vertical_program(version), executed([FINAL, *labels(version)]))
        add(version, 'plain-record', '''var label tag = label.new(0, 10, "plain")
var label gone = label.new(0, 20)
if bar_index == 0
    label.set_y(tag, na)
    label.set_yloc(tag, yloc.belowbar)
    label.delete(gone)
    label.set_yloc(gone, "unused")
if bar_index == 1
    label.set_xy(tag, 2, na)
''', executed([dict(id=1, x=2, y=None, text='plain', xloc='bar_index', yloc='belowbar')]))
        add(version, 'invalid-constructor-mode', 'label.new(1, na, yloc="unknown")\n',
            rejected('LABEL_YLOC', 'RUNTIME_ERROR'))
        add(version, 'invalid-setter-mode', 'label.set_yloc(label.new(1, na), "unknown")\n',
            rejected('LABEL_YLOC', 'RUNTIME_ERROR'))
        add(version, 'invalid-price-type', 'label.set_y(label.new(1, na), "unknown")\n', rejected('ARGUMENT_TYPE'))
    add(4, 'point-version', 'label.new(point=chart.point.from_index(0, na), yloc=yloc.abovebar)\n', rejected('POINT_VERSION'))
    files['bars.json'] = (ROOT / 'conformance/drawing-xloc/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored raw label states, mode changes and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/label-yloc'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Label vertical-coordinate fixtures verified.')


if __name__ == '__main__':
    main()
