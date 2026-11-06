"""Independent price-label appearance and version observations."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

TITLE = 'Label appearance'
LEGACY = dict(id=1, x=0, y=10., text='start')
START = dict(LEGACY, xloc='bar_index', color='#12345680', style='up', textcolor='#FFFFFFFF', size='tiny')
PREVIEW = {**START, 'color': '#11223300', 'style': 'left', 'textcolor': '#FFD000FF', 'size': 'small'}
CLOSED = {**START, 'color': '#AA550080', 'style': 'right', 'textcolor': '#F0F0F0FF', 'size': 'large'}
POINT = dict(id=2, x=150000, y=16., text='point', xloc='bar_time',
             color=None, style='center', textcolor='#334455FF', size='huge')


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}", overlay=true)\n{body}'


def changed_size(record, version, preview=False):
    if version != 6:
        return record
    return {**record, 'size': 19 if preview else 20}


def final_label(version):
    return {**changed_size(CLOSED, version), 'x': 150000, 'y': 12., 'text': 'moved', 'xloc': 'bar_time'}


def appearance(version):
    body = """var label tag = label.new(0, 10, "start", color=#12345680, style=label.style_label_up, size=size.tiny)
label alias = tag
"""
    if version >= 5:
        body += """var label pointTag = label.new(chart.point.from_time(150000, 16), "point", xloc.bar_time, yloc.price, na, label.style_label_center, #334455, size.huge)
"""
    size = 'int(close)' if version == 6 else 'close < 20 ? size.small : size.large'
    return body + f"""if bar_index == 1
    label.set_color(alias, close < 20 ? #11223300 : #AA550080)
    label.set_style(style=close < 20 ? label.style_label_left : label.style_label_right, id=alias)
    label.set_textcolor(alias, close < 20 ? #FFD000 : #F0F0F0)
    label.set_size(alias, {size})
if bar_index == 2
    label.set_text(tag, "moved")
    label.set_xy(tag, 1, 12)
    label.set_xloc(tag, 150000, xloc.bar_time)
"""


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected):
        case = f'v{version}-{name}'
        file = case + '.pine'
        files[file] = source(version, body).encode()
        cases.append(dict(case_id=case, language_version=version, source=file, bars='bars.json', expected=expected))

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    def executed(*labels):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], labels=list(labels))

    for version in range(1, 4):
        add(version, 'constructor-version', 'label.new(0, 10, color=color.blue)\n', rejected('LABEL_VERSION'))
        for name, value in (('color', 'na'), ('style', '"none"'), ('textcolor', 'na'), ('size', '12')):
            add(version, name + '-setter-version', f'label.set_{name}(na, {value})\n', rejected('LABEL_VERSION'))

    for version in (4, 5, 6):
        expected = [final_label(version)] + ([POINT] if version >= 5 else [])
        add(version, 'appearance', appearance(version), executed(*expected))
        add(version, 'legacy-promotion', '''var label old = label.new(0, 10, "start")
var label gone = label.new(0, 10)
if bar_index == 0
    label.delete(gone)
    label.set_style(gone, "invalid")
    label.set_size(na, "invalid")
if bar_index == 1
    label.set_size(old)
''', executed(dict(LEGACY, xloc='bar_index', color='#2196F3FF', style='down', textcolor='#FFFFFFFF',
                  size='normal' if version == 6 else 'auto')))
        add(version, 'coordinate-positional',
            'var label item = label.new(150000, 10, "start", xloc.bar_time, yloc.price, #10111280, label.style_none, na, size.normal)\n',
            executed(dict(LEGACY, x=150000, xloc='bar_time', color='#10111280', style='none', textcolor=None, size='normal')))
        add(version, 'price-only', 'label.new(0, 10, yloc=yloc.abovebar)\n',
            executed(*(dict(id=index, x=0, y=10., text='', xloc='bar_index', yloc='abovebar',
                            color='#2196F3FF', style='down', textcolor='#FFFFFFFF', size='normal')
                       for index in range(1, 5))))
        add(version, 'bad-style', 'label.set_style(label.new(0, 10), "unsupported")\n',
            rejected('LABEL_STYLE', 'RUNTIME_ERROR'))
        add(version, 'bad-size', 'label.new(0, 10, size="unsupported")\n',
            rejected('LABEL_SIZE', 'RUNTIME_ERROR'))
    for version in (4, 5):
        add(version, 'constructor-integer-size', 'label.new(0, 10, size=12)\n', rejected('ARGUMENT_TYPE'))
        add(version, 'setter-integer-size', 'label.set_size(label.new(0, 10), 12)\n', rejected('ARGUMENT_TYPE'))
    add(4, 'point-version', 'label.new(point=chart.point.from_index(0, 10), size=size.small)\n', rejected('POINT_VERSION'))
    add(6, 'integer-zero', 'var label item = label.new(0, 10, size=0)\n',
        executed(dict(id=1, x=0, y=10., text='', xloc='bar_index', color='#2196F3FF', style='down',
                      textcolor='#FFFFFFFF', size=0)))
    add(6, 'integer-negative', 'label.set_size(label.new(0, 10), -1)\n', rejected('LABEL_SIZE', 'RUNTIME_ERROR'))
    files['bars.json'] = (ROOT / 'conformance/drawing-xloc/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored raw label records and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/label-styles'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Label appearance fixtures verified.')


if __name__ == '__main__':
    main()
