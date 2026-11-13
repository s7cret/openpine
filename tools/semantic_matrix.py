"""Project the pinned Pine function overloads and OpenPine's historical ABI."""
import argparse
from collections import Counter, defaultdict
import re

from packages.contracts import HISTORICAL_PROFILE_ID, arrays, maps, matrices
from packages.contracts.colors import APPEARANCE_SUFFIX
from packages.contracts.chart_scale import TYPE as CHART_SCALE_TYPE
from packages.contracts.inputs import input_policy
from packages.contracts.markers import MARKER_KINDS
from packages.contracts.scopes import QUALIFIERS
from packages.contracts.visual_ohlc import KINDS as OHLC_KINDS
from packages.contracts.visual_display import DESTINATION_TYPE, DISPLAY_TYPE, FIELD as DISPLAY_FIELD
from packages.compiler.semantic.methods import drawing_method
from packages.execution.session import Session
from tools.specification import ROOT, read_json


OUTPUT = ROOT / 'docs/PINE_FUNCTION_COVERAGE.md'
# Narrow reviews of the complete pinned signature and behavior. New admissions
# start as PARTIAL until the entire pinned overload has been reviewed.
FULL = {'chart.point.from_index': 'CHART_POINTS.md', 'chart.point.from_time': 'CHART_POINTS.md'}
LIMITS = {
    'alert': 'локальные события replay, три режима частоты и ограниченное сообщение · [Профиль](ALERTS.md)',
    'fill': 'однотипные ссылки, первые три аргумента позиционные',
    'box.new': 'индикаторы и стратегии, координаты по допущенным профилям xloc',
    'label.new': 'индикаторы и стратегии, координаты по допущенным профилям xloc и yloc',
    'line.new': 'индикаторы и стратегии, закрытые xloc и стили, ширина 1–64',
    'polyline.new': 'индикаторы и стратегии, только curved=false и прямые сегменты, ширина 1–64, лимит 512 вершин',
    'linefill.new': 'индикаторы и стратегии, заливка видна только в общей панели обеих линий',
    'table.merge_cells': 'прямоугольники без пересечений, сохранение диапазона при очистке',
    'table.cell': 'размеры 0–100% и типографика с границами по версии',
    'table.new': 'индикаторы и стратегии, девять позиций, 256 ячеек, именованные frame_color, frame_width, border_color, border_width',
    'plot': 'статические параметры оформления',
    'plotarrow': 'статический offset и высота 1–1000 пикселей',
    'plotbar': 'первые шесть аргументов позиционные',
    'plotcandle': 'граница позиционных аргументов зависит от версии',
    'request.security': 'закрытые графы контекстов и вычисляемых целей',
    'request.security_lower_tf': 'закрытый профиль выражений и дочерних событий',
}
TYPE = re.compile(r'(?:((?:const|input|simple|series))\s+)?([\w.]+(?:<[^<>]+>)?)\Z')


def spelling(text):
    if text.startswith('series[') and text.endswith(']'):
        text = 'series ' + text[7:-1]
    parsed = TYPE.fullmatch(text)
    if not parsed or parsed[2] in QUALIFIERS:
        return None
    qualifier, base = parsed.groups()
    base = {'integer': 'int', 'color_series': 'color', 'strategy_direction': 'string',
            'plot': 'plot_id', 'hline': 'hline_id'}.get(base, base)
    if base.startswith('array<'):
        base = 'array.' + base[6:-1]
    return base, qualifier


def catalog_calls(catalog):
    calls = {name: list(variants) for name, variants in catalog.signatures.items()}
    plots=[('plot',prefix+suffix) for prefix in ('color_plot','band_plot')
           for suffix in ('','_overlay','_window','_appearance','_style_first')]
    for name, attribute in (*plots,('na', 'color_na')):
        pair = getattr(catalog, attribute, None)
        if pair is not None:
            calls.setdefault(name, []).append(pair)
    for name in ('plot','nz'):
        calls.setdefault(name, []).append(catalog.band_calls['bands.'+name])
    for variants in (catalog.signal_overlays, catalog.signal_windows):
        for name, pair in variants.items():
            calls.setdefault(name, []).append(pair)
    for name in ('partial_exits', 'pending_entries', 'order_groups', 'trailing'):
        for source, pair in getattr(catalog, name).items():
            calls.setdefault(source, []).append(pair)
    for name in ('lot_header', 'economics_header', 'intrabar_header', 'cycle_header', 'magnifier_header'):
        pair = getattr(catalog, name, None)
        if pair is not None:
            calls.setdefault('strategy', []).append(pair)
    for variants in (catalog.dynamic_windows, catalog.dynamic_signals):
        for name, pairs in calls.items():
            for signature, contract in tuple(pairs):
                selected = variants.get(contract.overload_id) if contract else None
                if selected:
                    calls[name].append((signature, selected[1]))
    for pairs in calls.values():
        pairs.extend(catalog.channel_windows[c.overload_id] for _, c in tuple(pairs)
                     if c and c.overload_id in catalog.channel_windows)
    for pairs in calls.values():
        base = tuple(pairs)
        for variants in (catalog.display_calls, catalog.destination_calls):
            pairs.extend(variants[contract.overload_id] for _, contract in base
                         if contract and contract.overload_id in variants)
        for _, contract in base:
            if contract:
                pairs.extend(catalog.fill_alert_calls.get(contract.overload_id, ()))
    from packages.compiler.semantic.number_format import extend
    for pairs in calls.values():
        pairs.extend(extend(catalog, signature, contract) for signature, contract in tuple(pairs)
                     if contract and contract.overload_id in catalog.format_contracts)
    from packages.compiler.semantic.chart_scale import extend as extend_scale
    for pairs in calls.values():
        pairs.extend(extend_scale(catalog, signature, contract) for signature, contract in tuple(pairs)
                     if contract and contract.overload_id in catalog.scale_contracts)
    from packages.compiler.semantic.visual_order import extend as extend_order
    for pairs in calls.values():
        pairs.extend(extend_order(catalog, signature, contract) for signature, contract in tuple(pairs)
                     if contract and contract.overload_id in catalog.visual_order_contracts)
    from packages.compiler.semantic.drawing_limits import extend as extend_limits
    for pairs in calls.values():
        pairs.extend(extend_limits(catalog, signature, contract) for signature, contract in tuple(pairs)
                     if contract and contract.overload_id in catalog.drawing_limit_contracts)
    return calls


def direct_form(name, version):
    if name == 'input' or version >= 5 and name in {
            'input.' + kind for kind in (*input_policy(version)['types'], 'symbol', 'timeframe', 'color', 'enum')}:
        return 'прямое глобальное объявление, ограниченные defaults и метаданные', 'INPUTS.md'
    family, _, member = name.partition('.')
    if family == 'array' and version >= 4:
        typed = member.removeprefix('new_')
        if member in ('new_line', 'new_label', 'new_box') or version >= 5 and member in ('first', 'last'):
            return 'массивы графических ID и действующие лимиты', 'DRAWING_ARRAYS.md'
        if member == 'new<type>' and version >= 5:
            return 'закрытый набор типов, в том числе графические ID', 'ARRAYS.md'
        if member.startswith('new_') and typed in (*arrays.ELEMENTS, 'color'):
            return 'закрытый набор типов элементов и лимиты массива', 'ARRAYS.md'
        if member in arrays.OPERATIONS and member not in ('new', 'is_na'):
            return 'закрытый набор типов элементов, операций и лимитов', 'ARRAYS.md'
    for namespace, module, page in (('map', maps, 'MAPS.md'), ('matrix', matrices, 'MATRICES.md')):
        operation = member.split('<', 1)[0]
        if family == namespace and version >= 5 and operation in module.OPERATIONS and operation != 'is_na':
            return 'закрытый набор типов и лимиты коллекции', page
    return None


def candidate_limits(reference, signature, contract, version, allow_widening):
    """Return gaps for a matching slice, or None for a different overload."""
    parameters = reference['parameters']
    by_name = {p['name']: p for p in parameters}
    names = [p['name'] for p in signature['parameters']]
    if version < 3 and reference['name'] in OHLC_KINDS and not parameters:
        return ['базовая сигнатура реконструирована по историческим источникам']
    if version < 4 and reference['basis'] in ('unverified', 'version_example') and set(by_name) <= set(names):
        return ['параметры исторического источника восстановлены частично']
    if not set(names) <= by_name.keys() or not names and parameters:
        return None
    gaps = []
    # In v1–v4 the source can collect both endpoint kinds in one record.
    alternatives = {'plot1', 'plot2', 'hline1', 'hline2'} if reference['name'] == 'fill' else set()
    missing = [p['name'] for p in parameters if p['name'] not in names and p['name'] not in alternatives]
    if missing:
        gaps.append('параметры вне профиля ' + ', '.join(missing))
    pinned_order = [p['name'] for p in parameters if p['name'] in names]
    positional = (6 if contract.operation.startswith('bands.gradient_') else 3) if reference['name'] == 'fill' else len(names)
    if names[:positional] != pinned_order[:positional] and not signature['binding']['named']:
        if version>=3 or reference['name']!='plot' or APPEARANCE_SUFFIX not in contract.overload_id:
            return None
        syntax_orders = ([part.split(':',1)[0].strip() for part in s.split('(',1)[1].split(')',1)[0].split(',')]
                         for s in reference['syntax_spellings'] if s.startswith('plot('))
        if not any(order[:positional]==names for order in syntax_orders):
            return None
    for name, supported, limit in zip(names, contract.parameter_types, contract.qualifier_limits, strict=True):
        choices = [spelling(text) for text in by_name[name]['type_spellings']]
        known = [choice for choice in choices if choice is not None]
        accepted = set(supported)
        if version <= 4 and name == 'style' and 'plot_style' in accepted:
            accepted.add('int')  # Legacy reference spells the named styles as integers.
        if name == 'scale' and CHART_SCALE_TYPE in accepted:
            if version <= 4:
                accepted.add('int')
            gaps.append('scale.left/right/none, scale.none только с overlay')
        if name == DISPLAY_FIELD and DISPLAY_TYPE in accepted:
            if version == 4:
                accepted.add('int')  # The v4 reference uses integer for the two named modes.
            gaps.append('display ограничен display.all и display.none')
        if name == DISPLAY_FIELD and DESTINATION_TYPE in accepted:
            accepted.add(DISPLAY_TYPE)
            surfaces = ('панель, статус и окно данных' if reference['name'] in MARKER_KINDS
                        else 'четыре места вывода')
            gaps.append(surfaces + ', константы и выбор через булев input')
        if allow_widening and 'float' in accepted:
            accepted.add('int')
        overlap = [base in accepted for base, _ in known]
        if known and len(known) == len(choices) and not any(overlap):
            return None
        if not choices or len(known) != len(choices):
            gaps.append(f'тип {name} в источнике описан неполно')
        elif not all(overlap):
            gaps.append(f'часть типов {name} вне профиля')
        if any(base in accepted and qualifier and QUALIFIERS.index(qualifier) > QUALIFIERS.index(limit)
               for base, qualifier in known):
            gaps.append(f'{name} ограничен {limit}')
    if reference['name'] in FULL:
        result = getattr(contract.result_type, 'base', None), getattr(contract.result_type, 'qualifier', None)
        if not any(value and value[0] == result[0] and value[1] in (None, result[1])
                   for value in map(spelling, reference['result_type_spellings'])):
            gaps.append('результат отличается от закреплённого')
        if not signature['binding']['named'] or any(p['requiredness']['state'] != 'required' for p in signature['parameters']):
            gaps.append('режим связывания аргументов отличается')
    return gaps


def classify(reference, calls, version, allow_widening):
    name = reference['name']
    candidates = []
    for signature, contract in calls.get(name, ()):
        if contract is not None:
            gaps = candidate_limits(reference, signature, contract, version, allow_widening)
            if gaps is not None:
                candidates.append((len(signature['parameters']), gaps, contract.operation))
    if candidates:
        _, gaps, operation = min(candidates, key=lambda candidate: (-candidate[0], len(candidate[1])))
        if not gaps and name in FULL and version >= 5:
            return 'FULL', f'[{operation}]({FULL[name]})'
        if operation.startswith('bands.gradient_'):
            gaps = [*gaps,'маска из двух plot, первые шесть аргументов позиционные']
        elif name in LIMITS:
            gaps = [*gaps, LIMITS[name]]
        if name == 'alertcondition':
            scope = 'архивные формы и буквальное сообщение' if version < 4 else 'журнал replay, подстановки в отдельном профиле'
            gaps.append(f'глобальные условия, {scope} · [Профиль](ALERTS.md)')
        if operation.startswith('indicator_window.'):
            length = 'четыре отсчёта' if name.removeprefix('ta.') == 'swma' else (
                'постоянная длина 1–100 000' if version < 4 else 'длина series int 1–100 000')
            gaps.append(f'{length}, [границы модели](WINDOW_INDICATORS.md)')
        if operation.startswith('channel.'):
            length = 'длина simple int' if operation in ('channel.kc', 'channel.kcw') else 'длина series int'
            gaps.append(f'{length} 1–100 000 · [профиль](CHANNEL_INDICATORS.md)')
        if name=='plot':
            gaps.append('статический offset в пределах ±10 000')
            if version<3:
                gaps.append('архивные позиционные формы')
        operations = sorted({operation for _, _, operation in candidates}) if name == 'fill' else [operation]
        return 'PARTIAL', ' / '.join(f'`{op}`' for op in operations) + ' · ' + (' · '.join(gaps) or 'полный контракт не подтверждён')
    special = direct_form(name, version)
    if special:
        scope, page = special
        return 'PARTIAL', f'[{scope}]({page})'
    return 'MISSING', 'нет совпадающего допуска для перегрузки'


def matrix():
    session = Session(profile_id=HISTORICAL_PROFILE_ID)
    rows, availability = {}, defaultdict(set)
    for version, reference in sorted(session.snapshot.references.items()):
        catalog = session.catalog_for(version)
        calls = catalog_calls(catalog)
        allow_widening = ['int', 'float'] in catalog.rules['rule.types']['implicit_conversions']
        rows[version] = []
        for entry in sorted(reference['entries'], key=lambda item: (item['name'], item['entry_id'])):
            if entry['category'] != 'functions':
                continue
            availability[entry['name']].add(version)
            status, scope = classify(entry, calls, version, allow_widening)
            receiver, _, member = entry['name'].partition('.')
            if status != 'MISSING' and drawing_method(catalog, receiver, member):
                scope += ' · вызов через объект'
            suffix = entry['entry_id'].partition(':overload:')[2]
            rows[version].append((entry['name'], suffix or '—', status, scope))
    return rows, availability


def render():
    rows, availability = matrix()
    lines = ['# Покрытие функций Pine в OpenPine', '',
             'Статическая матрица исторического профиля. Источник строк — [закреплённый bundle](../spec/lock.json).',
             'Номер перегрузки берётся из её ID внутри версии, `—` обозначает запись без отдельного номера.',
             '`FULL` означает проверенный полный контракт по закреплённому описанию, `PARTIAL` — реализованный срез,',
             '`MISSING` — отсутствие совпадающего допуска, `N/A` — отсутствие функции в закреплённой версии.',
             'Актуальная приёмка и её SHA указаны в [матрице семантики](SEMANTIC_COVERAGE.md).',
             'Сверка с исполнением TradingView учитывается отдельно.',
             'Пометка «вызов через объект» указывает на [методную форму графической операции](METHODS.md#методы-графических-объектов).', '',
             '| Pine | FULL | PARTIAL | MISSING |', '| --- | ---: | ---: | ---: |']
    for version, entries in rows.items():
        counts = Counter(row[2] for row in entries)
        lines.append(f"| v{version} | {counts['FULL']} | {counts['PARTIAL']} | {counts['MISSING']} |")
    for version, entries in rows.items():
        lines.extend(('', f'## Pine v{version}', '', '| Функция | Перегрузка | Статус | Граница |', '| --- | ---: | --- | --- |'))
        lines.extend(f'| `{name}` | {overload} | {status} | {scope} |' for name, overload, status, scope in entries)
    lines.extend(('', '## Недоступность по версиям', '', '| Функция | N/A |', '| --- | --- |'))
    for name, versions in sorted(availability.items()):
        absent = set(rows) - versions
        if absent:
            lines.append(f'| `{name}` | ' + ', '.join(f'v{version}' for version in sorted(absent)) + ' |')
    lines.extend(('', '## Дополнительные сигнатуры', '',
                  'В v5–v6 также допущен вертикальный градиент между двумя `hline`.',
                  'Эта сигнатура подтверждена материалами TradingView и учитывается отдельно от строк закреплённого справочника.',
                  '[ADR 0032](adr/0032-gradient-fill.md)'))
    from packages.contracts.drawing_objects import ALL_KINDS
    lines.extend(('', '## Переменные графических объектов', '',
                  '| Переменная | v1–v4 | v5–v6 | Граница |',
                  '| --- | --- | --- | --- |'))
    for name in sorted(ALL_KINDS):
        lines.append(f'| `{name}` | N/A | PARTIAL | [Снимок живых ID, чтение и перебор](DRAWING_OBJECTS.md) |')
    lines.extend(('', '## Инвентарь сценариев', '', 'Здесь указаны ожидаемые исходы по файлам сценариев.', '',
                  '| Профиль | Исполнения | Ожидаемые отказы |', '| --- | ---: | ---: |'))
    for path in sorted((ROOT/'conformance').glob('*/cases.json')):
        counts = Counter(case['expected']['status'] for case in read_json(path)['cases'])
        link = path.relative_to(ROOT).as_posix()
        lines.append(f"| [{path.parent.name}](../{link}) | {counts['executed']} | {counts['rejected']} |")
    return ('\n'.join(lines) + '\n').encode('utf-8')


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--check', action='store_true')
    args = parser.parse_args(argv)
    raw = render()
    if args.check:
        if not OUTPUT.is_file() or OUTPUT.read_bytes() != raw:
            raise SystemExit('Static function coverage is stale. Run python -m tools.semantic_matrix.')
    else:
        OUTPUT.write_bytes(raw)
    print('Static function coverage verified.')


if __name__ == '__main__':
    main()
