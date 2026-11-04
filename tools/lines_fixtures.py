"""Generate fixed bar-index line fixtures with independent expectations."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

MOVING = """var line track = na
var label tag = label.new(na, na, "moving")
if bar_index == 0
    track := line.new(na, na, na, na)
alias = track
line.set_xy1(alias, 0, 10)
line.set_xy2(alias, bar_index, close)
label.set_xy(tag, bar_index, close)
if bar_index == 1
    temporary = line.new(x1=0, y1=20, x2=0, y2=25)
    line.delete(temporary)
    line.set_xy2(temporary, 10, 11)
    line.delete(temporary)
line.delete(na)
"""


def source(version, body=MOVING):
    declaration = 'indicator' if version >= 5 else 'study'
    return f'//@version={version}\n{declaration}("Lines", overlay=true)\n{body}'


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

    def executed(lines, labels):
        return dict(status='executed', title='Lines', shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], lines=lines, labels=labels)

    for version in range(1, 4):
        add(version, 'call-version', 'line.new(0, 1, 2, 3)\n', rejected('LINE_VERSION'))
        add(version, 'type-version', 'line item = na\nplot(close)\n', rejected('LINE_VERSION'))
    for version in range(4, 7):
        add(version, 'moving', MOVING, executed(
            [dict(id=1, x1=0, y1=10., x2=3, y2=40.)], [dict(id=1, x=3, y=40., text='moving')]))
        add(version, 'missing-endpoint', 'var line item = line.new(na, 10, 1, 20)\nline.set_xy1(na, 0, 1)\n',
            executed([dict(id=1, x1=None, y1=10., x2=1, y2=20.)], []))
        add(version, 'wrong-kind', 'line.delete(label.new(0, close))\n', rejected('ARGUMENT_TYPE'))
        add(version, 'outside-slice', 'line.new(0, 1, 2, 3, xloc="bar_time")\n', rejected('DRAWING_XLOC'))
        if version >= 5:
            add(version, 'varip-line', 'varip line item = line.new(0, 1, 2, 3)\n',
                rejected('LINE_BINDING', 'PROFILE_UNCOVERED'))
    files['bars.json'] = canonical(dict(schema_version='openpine.historical-bars.2', context_id='lines.test',
        symbol='TEST:LINES', timeframe='1', bars=[
            dict(time=i * 60000, open=c - 1, high=c + 2, low=c - 2, close=c, volume=1.)
            for i, c in enumerate((10., 20., 30., 40.))])) + b'\n'
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Fixed OHLC, manual endpoint and alias expectations, explicit Pine version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/lines'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Line fixtures verified.')


if __name__ == '__main__':
    main()
