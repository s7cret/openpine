'''Independent expectations for box text, constructors and version gates.'''
import argparse

from packages.contracts.wire import canonical
from tools.specification import ROOT

TITLE = 'Box text'
LEGACY = dict(id=1, left=0, top=35., right=2, bottom=5., border_color='#2196F3FF', bgcolor='#2196F3FF')
START = dict(LEGACY, border_color=None, bgcolor=None, xloc='bar_index', border_width=1,
             border_style='solid', extend='none', text='Initial zone', text_size='auto',
             text_color='#363A45FF', text_halign='center', text_valign='center',
             text_wrap='none', text_font_family='default', text_formatting=0)
POINT = dict(START, id=2, left=60000, top=28., right=240000, bottom=8., xloc='bar_time',
             border_color='#123456FF', bgcolor='#33669940', border_width=2, border_style='dashed',
             extend='right', text='Point zone', text_size='small', text_color='#FFFFFFFF',
             text_halign='right', text_valign='bottom', text_wrap='auto', text_font_family='monospace')


def source(version, body):
    declaration = 'study' if version < 5 else 'indicator'
    return f'//@version={version}\n{declaration}("{TITLE}", overlay=true)\n{body}'


def changes(version, preview=False):
    size = (12 if preview else 18) if version == 6 else ('tiny' if preview else 'large')
    fields = dict(text='Open zone' if preview else 'Closed zone', text_size=size, text_color='#44CCEEFF',
                  text_halign='left', text_valign='top', text_wrap='auto', text_font_family='monospace')
    if version == 6:
        fields['text_formatting'] = 2 if preview else 3
    rows = []
    row = START
    for name, value in fields.items():
        row = dict(row, **{name: value})
        rows.append(row)
    return rows


def final_updates(version):
    moved = dict(changes(version)[-1], left=1, top=36.)
    return [moved, dict(moved, text='Final\nzone')]


def text_program(version):
    sizes = '12 : 18' if version == 6 else 'size.tiny : size.large'
    formatting = ', text.format_italic' if version == 6 else ''
    body = f'''var box zone = box.new(0, 35, 2, 5, border_color=na, bgcolor=na, text="Initial zone")
box alias = zone
var box points = box.new(chart.point.from_time(60000, 28), chart.point.from_time(240000, 8), #123456, 2, line.style_dashed, extend.right, xloc.bar_time, #33669940, "Point zone", size.small, color.white, text.align_right, text.align_bottom, text.wrap_auto, font.family_monospace, false{formatting})
if bar_index == 1
    box.set_text(text=close < 20 ? "Open zone" : "Closed zone", id=alias)
    box.set_text_size(alias, close < 20 ? {sizes})
    box.set_text_color(alias, #44CCEE)
    box.set_text_halign(alias, text.align_left)
    box.set_text_valign(alias, text.align_top)
    box.set_text_wrap(alias, text.wrap_auto)
    box.set_text_font_family(alias, font.family_monospace)
'''
    if version == 6:
        body += '    box.set_text_formatting(alias, close < 20 ? text.format_italic : text.format_bold + text.format_italic)\n'
    return body + r'''if bar_index == 2
    box.set_lefttop(zone, 1, 36)
    box.set_text(zone, "Final\nzone")
'''


def point(version):
    return dict(POINT, text_formatting=2 if version == 6 else 0)


OVERLAY_LABELS = (
    dict(id=1, x=0, y=15., text='Pane label', xloc='bar_index', yloc='price', color='#2196F3FF',
         style='down', textcolor='#FFFFFFFF', size='normal', textalign='center', tooltip='pane',
         text_font_family='default', text_formatting=0),
    dict(id=2, x=1, y=None, text='Main label', xloc='bar_index', yloc='abovebar', color='#2196F3FF',
         style='down', textcolor='#FFFFFFFF', size='normal', textalign='center', tooltip='price',
         text_font_family='default', text_formatting=0, force_overlay=True),
)
OVERLAY_BOXES = (
    dict(START, top=34., right=3, bottom=6., text='Pane zone', border_color='#2196F3FF', bgcolor='#2196F3FF'),
    dict(START, id=2, top=32., bottom=8., text='Price zone', border_color='#2196F3FF', bgcolor='#2196F3FF',
         force_overlay=True),
)


def overlay_updates(preview=False):
    label = dict(OVERLAY_LABELS[1], tooltip='Opening' if preview else 'Closed')
    box = dict(OVERLAY_BOXES[1], text='Preview zone' if preview else 'Final zone')
    if preview:
        return [label], [box]
    moved_label = dict(label, x=2)
    moved_box = dict(box, right=3, bottom=7.)
    return [label, moved_label, dict(moved_label, textcolor='#EEDD22FF')], [
        box, moved_box, dict(moved_box, border_width=2)]


def overlay_program(version):
    label_point = 'chart.point.from_index(1, na)'
    box_points = 'chart.point.from_index(0, 32), chart.point.from_index(2, 8)'
    if version == 5:
        main_label = (f'label.new({label_point}, "Main label", xloc.bar_index, yloc.abovebar, color.blue, '
                      'label.style_label_down, color.white, size.normal, text.align_center, "price", font.family_default, 1 < 2)')
        main_box = f'box.new({box_points}, text="Price zone", force_overlay=true)'
    else:
        main_label = f'label.new({label_point}, "Main label", yloc=yloc.abovebar, tooltip="price", force_overlay=true)'
        main_box = (f'box.new({box_points}, color.blue, 1, line.style_solid, extend.none, xloc.bar_index, color.blue, '
                    '"Price zone", size.auto, color.black, text.align_center, text.align_center, text.wrap_none, '
                    'font.family_default, 1 < 2)')
    return f"""var label paneLabel = label.new(0, 15, "Pane label", tooltip="pane", force_overlay=false)
var label mainLabel = {main_label}
var box paneBox = box.new(0, 34, 3, 6, text="Pane zone", force_overlay=false)
var box mainBox = {main_box}
if bar_index == 1
    label.set_tooltip(mainLabel, close < 20 ? "Opening" : "Closed")
    box.set_text(mainBox, close < 20 ? "Preview zone" : "Final zone")
if bar_index == 2
    label.set_xy(mainLabel, 2, na)
    label.set_textcolor(mainLabel, #EEDD22)
    box.set_rightbottom(mainBox, 3, 7)
    box.set_border_width(mainBox, 2)
"""


def fixtures():
    files, cases = {}, []

    def add(version, name, body, expected, *, overlay=True):
        case = f'v{version}-{name}'
        file = case + '.pine'
        program = source(version, body)
        if not overlay:
            program = program.replace('overlay=true)', 'overlay=false)', 1)
        files[file] = program.encode()
        cases.append(dict(case_id=case, language_version=version, source=file, bars='bars.json', expected=expected))

    def executed(rows):
        return dict(status='executed', title=TITLE, shorttitle='', overlay=True, observation_count=4,
                    outputs=[], qualifiers=[], labels=[], lines=[], boxes=rows)

    def rejected(code, category='LANGUAGE_INVALID'):
        return dict(status='rejected', category=category, code=code)

    for version in range(1, 5):
        code = 'BOX_VERSION' if version < 4 else 'BOX_TEXT_VERSION'
        add(version, 'text-constructor-version', 'box.new(0, 35, 2, 5, text="zone")\n', rejected(code))
        add(version, 'text-setter-version', 'box.set_text(na, "zone")\n', rejected(code))
    add(4, 'plain-box', 'var box zone = box.new(0, 35, 2, 5)\n', executed([LEGACY]))
    add(4, 'positional-text-version',
        'box.new(0, 35, 2, 5, na, 1, line.style_solid, extend.none, xloc.bar_index, na, "zone")\n',
        rejected('BOX_TEXT_VERSION'))
    for version in (5, 6):
        add(version, 'text-attributes', text_program(version), executed([final_updates(version)[-1], point(version)]))
        add(version, 'legacy-setter', 'var box zone = box.new(0, 35, 2, 5)\nif bar_index == 1\n    box.set_text(zone, "legacy")\n',
            executed([dict(START, border_color=LEGACY['border_color'], bgcolor=LEGACY['bgcolor'], text='legacy')]))
        for name, value, code in (
                ('text_halign', 'text.align_top', 'BOX_TEXT_HALIGN'),
                ('text_valign', 'text.align_left', 'BOX_TEXT_VALIGN'),
                ('text_wrap', '"auto"', 'BOX_TEXT_WRAP'),
                ('text_font_family', '"serif"', 'BOX_TEXT_FONT_FAMILY')):
            add(version, name + '-invalid', f'box.new(0, 35, 2, 5, {name}={value})\n', rejected(code, 'RUNTIME_ERROR'))
    add(5, 'numeric-size-version', 'box.new(0, 35, 2, 5, text_size=18)\n', rejected('ARGUMENT_TYPE'))
    add(5, 'format-constructor-version', 'box.new(0, 35, 2, 5, text_formatting=text.format_bold)\n', rejected('BOX_TEXT_VERSION'))
    add(5, 'format-setter-version', 'box.set_text_formatting(na, text.format_bold)\n', rejected('BOX_TEXT_VERSION'))
    add(6, 'constructor-forms', '''var box a = box.new(0, 35, 2, 5, na, 1, line.style_solid, extend.none, xloc.bar_index, na, "Positional", 18)
var box b = box.new(bottom_right=chart.point.from_index(2, 5), top_left=chart.point.from_index(0, 35), text="Named", text_size=0)
''', executed([dict(START, text='Positional', text_size=18),
               dict(START, id=2, text='Named', text_size=0, bgcolor=LEGACY['bgcolor'], border_color=LEGACY['border_color'])]))
    add(6, 'negative-size', 'box.new(0, 35, 2, 5, text_size=-1)\n', rejected('BOX_TEXT_SIZE', 'RUNTIME_ERROR'))
    add(6, 'invalid-format-type', 'box.set_text_formatting(box.new(0, 35, 2, 5), 3)\n', rejected('ARGUMENT_TYPE'))
    add(6, 'force-overlay-boundary', 'box.new(0, 35, 2, 5, text="zone", force_overlay=true)\n',
        executed([dict(START, id=i, text='zone', border_color='#2196F3FF', bgcolor='#2196F3FF', force_overlay=True)
                  for i in range(1, 5)]))
    for version in range(1, 5):
        add(version, 'force-overlay-version', 'box.new(0, 35, 2, 5, force_overlay=true)\n',
            rejected('BOX_VERSION' if version < 4 else 'BOX_TEXT_VERSION'))
    for version in (5, 6):
        add(version, 'force-overlay-series', 'box.new(0, 35, 2, 5, force_overlay=close > open)\n',
            rejected('ARGUMENT_QUALIFIER'))
        add(version, 'force-overlay-number', 'box.new(0, 35, 2, 5, force_overlay=1)\n',
            rejected('ARGUMENT_TYPE'))
        labels, boxes = overlay_updates()
        expected = dict(executed([OVERLAY_BOXES[0], boxes[-1]]), overlay=False,
                        labels=[OVERLAY_LABELS[0], labels[-1]])
        add(version, 'force-overlay-mixed', overlay_program(version), expected, overlay=False)
    files['bars.json'] = (ROOT / 'conformance/box-borders/bars.json').read_bytes()
    files['cases.json'] = canonical(dict(schema_version='openpine.conformance.cases.1',
        implementation_profile_id='openpine.historical-bars.1', rule_refs=[], assertion_refs=[], cases=cases,
        oracle=dict(kind='independent_manual_expectations', external_pine_execution=False,
                    basis='Hand-authored box text attributes, transparent and point boxes, and version boundaries.'))) + b'\n'
    return files


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument('--check', action='store_true')
    check = parser.parse_args(argv).check
    directory = ROOT / 'conformance/box-text'
    if not check:
        directory.mkdir(parents=True, exist_ok=True)
    for name, raw in fixtures().items():
        path = directory / name
        if check:
            assert path.read_bytes() == raw, name
        else:
            path.write_bytes(raw)
    print('Box text fixtures verified.')


if __name__ == '__main__':
    main()
