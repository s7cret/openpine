"""Hand-authored expectations for linefill identity and its two boundaries."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

CHANNEL = """var line upper = line.new(0, 20, 3, 35, extend=extend.right)
var line lower = line.new(0, 5, 3, 15, extend=extend.right)
var linefill area = linefill.new(upper, lower, #12345680)
linefill alias = area
if bar_index == 1
    line.set_xy2(linefill.get_line1(alias), 3, close < 20 ? 30 : 45)
    line.set_xy1(linefill.get_line2(alias), 0, 6)
    linefill.set_color(alias, close < 20 ? #FF000080 : na)
if bar_index == 2
    linefill.set_color(area, #33669980)
"""
DELETE = CHANNEL + """if bar_index == 3
    line.delete(lower)
    linefill.set_color(area, #FFFFFFFF)
"""
REPLACE = """var line first = line.new(0, 20, 3, 20)
var line second = line.new(0, 5, 3, 5)
var linefill old = linefill.new(first, second, #FF000080)
if bar_index == 0
    linefill current = linefill.new(second, first, #22446680)
    linefill.set_color(old, #FFFFFFFF)
    linefill.delete(old)
    line.set_xy2(linefill.get_line1(current), 3, 7)
    line.set_xy2(linefill.get_line2(current), 3, 23)
    linefill invalid = linefill.new(first, first, na)
    linefill.delete(invalid)
"""
UPPER = dict(id=1, x1=0, y1=20., x2=3, y2=45., extend='right')
LOWER = dict(id=2, x1=0, y1=6., x2=3, y2=15., extend='right')
FILL = dict(id=1, line1=1, line2=2, color='#33669980')


def source(version, body=CHANNEL, *, overlay=True):
    declaration = 'indicator' if version >= 5 else 'study'
    return f'//@version={version}\n{declaration}("Channel", overlay={str(overlay).lower()})\n{body}'


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        case_id = f'v{version}-{name}'
        filename = case_id + '.pine'
        files[filename] = source(version, body).encode()
        cases.append(dict(case_id=case_id, language_version=version, source=filename,
                          bars='bars.json', expected=expected))

    def rejected(code):
        return dict(status='rejected', category='LANGUAGE_INVALID', code=code)

    def executed(lines, fills):
        return dict(status='executed', title='Channel', shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], labels=[], lines=lines, boxes=[], tables=[], linefills=fills)

    for version in range(1, 5):
        add(version, 'call-version', 'linefill.delete(na)\n', rejected('LINEFILL_VERSION'))
        add(version, 'type-version', 'var linefill item = na\nplot(close)\n', rejected('LINEFILL_VERSION'))
    for version in (5, 6):
        add(version, 'channel', CHANNEL, executed([UPPER, LOWER], [FILL]))
        add(version, 'delete', DELETE, executed([UPPER], []))
        add(version, 'explicit-delete', CHANNEL + """if bar_index == 3
    linefill.delete(area)
    linefill.set_color(area, #FFFFFFFF)
""", executed([UPPER, LOWER], []))
        add(version, 'replace', REPLACE, executed([
            dict(id=1, x1=0, y1=20., x2=3, y2=23.), dict(id=2, x1=0, y1=5., x2=3, y2=7.)],
            [dict(id=2, line1=2, line2=1, color='#22446680')]))
        add(version, 'wrong-kind', 'linefill.delete(line.new(0, 10, 1, 5))\n', rejected('ARGUMENT_TYPE'))
    files['bars.json'] = (ROOT / 'conformance/boxes/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored line geometry, fill references, replacement, deletion and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/linefills'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Linefill fixtures verified.')


if __name__ == '__main__':
    main()
