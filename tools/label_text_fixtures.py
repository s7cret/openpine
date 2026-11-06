"""Independent label text, font, formatting and version expectations."""
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

TITLE = 'Label text'
START = dict(id=1, x=0, y=10., text='wide\nshort', xloc='bar_index', yloc='price', color='#12345680',
             style='center', textcolor='#FFFFFFFF', size='normal', textalign='left', tooltip='initial',
             text_font_family='default', text_formatting=0)
POINT = dict(START, id=2, x=60000, y=None, text='point', xloc='bar_time', yloc='abovebar',
             color='#2196F3FF', style='down', textalign='right', tooltip='point hint', text_font_family='monospace')


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}", overlay=true)\n{body}'


def labels(version):
    return [POINT] if version >= 5 else []


def changes(version, preview=False):
    row = dict(START, tooltip='open' if preview else 'closed')
    rows = [row]
    row = dict(row, textalign='center' if preview else 'right')
    rows.append(row)
    if version >= 5:
        row = dict(row, text_font_family='default' if preview else 'monospace')
        rows.append(row)
    if version >= 6:
        row = dict(row, text_formatting=2 if preview else 3)
        rows.append(row)
    row = dict(row, x=60000, xloc='bar_time')
    return [*rows, row, dict(row, yloc='belowbar')]


def final_label(version):
    return dict(changes(version)[-1], text='last\nline', tooltip='')


def text_program(version):
    body = 'var format = text.format_bold + text.format_italic\n' if version == 6 else ''
    body += r"""var label tag = label.new(0, 10, "wide\nshort", color=#12345680, style=label.style_label_center, textalign=text.align_left, tooltip="initial")
label alias = tag
"""
    if version >= 5:
        body += ('var label pointLabel = label.new(chart.point.from_time(60000, na), "point", xloc.bar_time, '
                 'yloc.abovebar, color.blue, label.style_label_down, color.white, size.normal, '
                 'text.align_right, "point hint", font.family_monospace)\n')
    body += """if bar_index == 1
    label.set_tooltip(tooltip=close < 20 ? "open" : "closed", id=alias)
    label.set_textalign(alias, close < 20 ? text.align_center : text.align_right)
"""
    if version >= 5:
        body += '    label.set_text_font_family(alias, close < 20 ? font.family_default : font.family_monospace)\n'
    if version >= 6:
        body += '    label.set_text_formatting(alias, close < 20 ? text.format_italic : format)\n'
    return body + r"""    label.set_xloc(alias, 60000, xloc.bar_time)
    label.set_yloc(alias, yloc.belowbar)
if bar_index == 2
    label.set_text(tag, "last\nline")
    label.set_tooltip(tag, "")
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

    for version in (1, 2, 3):
        for name, body in (('constructor','label.new(0, 10, tooltip="hint")'), ('tooltip','label.set_tooltip(na, "")'),
                           ('align','label.set_textalign(na, "")'), ('font','label.set_text_font_family(na, "")'),
                           ('format','label.set_text_formatting(na, 0)')):
            add(version, name + '-version', body + '\n', rejected('LABEL_VERSION'))
    for version in (4, 5, 6):
        add(version, 'text-attributes', text_program(version), executed([final_label(version), *labels(version)]))
        add(version, 'legacy-tooltip', '''var label tag = label.new(0, 10, "plain")
if bar_index == 1
    label.set_tooltip(tag, "more")
''', executed([dict(id=1, x=0, y=10., text='plain', xloc='bar_index', yloc='price',
                    textalign=None, tooltip='more', text_font_family='default', text_formatting=0)]))
        add(version, 'invalid-align', 'label.new(0, 10, textalign="top")\n', rejected('LABEL_TEXTALIGN', 'RUNTIME_ERROR'))
    add(4, 'font-setter-version', 'label.set_text_font_family(na, font.family_monospace)\n', rejected('LABEL_TEXT_VERSION'))
    add(4, 'font-constructor-version', 'label.new(0, 10, text_font_family=font.family_monospace)\n', rejected('LABEL_TEXT_VERSION'))
    for version in (4, 5):
        add(version, 'format-setter-version', 'label.set_text_formatting(na, text.format_none)\n', rejected('LABEL_TEXT_VERSION'))
        add(version, 'format-constructor-version', 'label.new(0, 10, text_formatting=text.format_bold)\n', rejected('LABEL_TEXT_VERSION'))
    add(5, 'invalid-font', 'label.set_text_font_family(label.new(0, 10), "serif")\n',
        rejected('LABEL_TEXT_FONT_FAMILY', 'RUNTIME_ERROR'))
    add(6, 'invalid-format-type', 'label.set_text_formatting(label.new(0, 10), 3)\n', rejected('ARGUMENT_TYPE'))
    add(6, 'invalid-format-operator', 'label.new(0, 10, text_formatting=text.format_bold - text.format_italic)\n',
        rejected('TEXT_FORMAT_OPERATOR'))
    add(6, 'format-default', r'''var label tag = label.new(0, 10, "wide\nshort", color=#12345680, style=label.style_label_center, textalign=text.align_left, tooltip="initial", text_formatting=text.format_bold)
if bar_index == 1
    label.set_text_formatting(tag)
''', executed([START]))
    add(6, 'force-overlay-boundary', 'label.new(0, 10, tooltip="hint", force_overlay=true)\n',
        rejected('LABEL_FORCE_OVERLAY', 'PROFILE_UNCOVERED'))
    files['bars.json'] = (ROOT / 'conformance/drawing-xloc/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored raw label text attributes, mode changes and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/label-text'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Label text fixtures verified.')


if __name__ == '__main__':
    main()
