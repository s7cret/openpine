[Приёмка привязки меток Pine к свечам](LABEL_YLOC_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1871 зарегистрированных тестов на каждой ОС, наблюдений conformance — 4046, программ корпуса — 180.

[Приёмка оформления ценовых меток Pine v4–v6](LABEL_STYLES_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1867 зарегистрированных тестов на каждой ОС, наблюдений conformance — 4021, программ корпуса — 180.

[Приёмка рамок ценовых зон Pine v4–v6](BOX_BORDERS_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1863 зарегистрированных тестов на каждой ОС, 3981 наблюдение conformance, 180 программ.

[Приёмка оформления линий Pine v4–v6](LINE_STYLES_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1859 зарегистрированных тестов на каждой ОС, 3951 наблюдение conformance, 180 программ.

[Приёмка смены координатного режима рисунков](DRAWING_XLOC_MUTATIONS_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1856 зарегистрированных тестов на каждой ОС, 3921 наблюдений conformance, 180 программ.

[Приёмка ломаных и полигонов Pine v5–v6](POLYLINES_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1853 зарегистрированных тестов на каждой ОС, 3886 наблюдений conformance, 180 программ.

[Приёмка точек графика Pine v5–v6](CHART_POINTS_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1850 зарегистрированных тестов на каждой ОС, 3858 наблюдений conformance, 180 программ.

[Приёмка временных координат рисунков Pine v4–v6](DRAWING_XLOC_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1847 зарегистрированных тестов на каждой ОС, 3836 наблюдений conformance, 180 программ.

[Приёмка заливки между линиями Pine v5–v6](LINEFILLS_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1844 зарегистрированных тестов на каждой ОС, 3813 наблюдений conformance, 180 программ.

[Приёмка закреплённых таблиц Pine v4–v6](TABLES_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1841 зарегистрированный тест на каждой ОС, 3795 наблюдений conformance, 180 программ.

[Приёмка ценовых зон Pine v4–v6](BOXES_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1838 test ID на ОС, 3771 conformance-наблюдений, 180 программ.

[Приёмка лучей и чтения цены линии](LINE_EXTENSIONS_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1835 test ID на ОС, 3750 conformance-наблюдений, 180 программ.

[Приёмка линий Pine v4–v6](LINES_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1832 test ID на ОС, 3735 conformance-наблюдений, 180 программ.

[Приёмка меток Pine v4–v6](LABELS_ACCEPTANCE.md). CI 57/57, Python 3.14.8, 1829 test ID на ОС, 3715 conformance-наблюдений, 180 программ.

[Приёмка сигнальных слоёв](VISUAL_SIGNALS_ACCEPTANCE.md): CI 57/57, Python3.14.8, 1826 test ID / 179 модулей на ОС, 3698 conformance и 180 программ. Bgcolor/barcolor/plotshape/plotchar v1–v6, OHLC UI, checkpoint/Parquet/range halo. Внешняя сверка отдельно.

[Приёмка уровней и зон](VISUAL_BANDS_ACCEPTANCE.md): CI 57/57, Python 3.14.8, 1817 test ID / 178 модулей на ОС, 3643 conformance-сценариев и 174 программы корпуса. Plot/hline/fill v1–v6, цветовые inputs v4–v6, checkpoint/Parquet/ranges/UI. Внешняя сверка TradingView отдельно.

[Приёмка цветов и plot](COLORS_ACCEPTANCE.md): CI 57/57, Python 3.14.8, 1801 test ID / 177 модулей на ОС, 3600 conformance-сценариев и 166 программ корпуса. Палитры по версиям, история/коллекции, Parquet, график и восстановление; v1–v3 реконструированы, внешняя сверка TradingView отдельно.

# Реализованная семантика Pine v1–v6

[Приёмка смешанных результатов](PRODUCT_RESULTS_ACCEPTANCE.md): CI 57/57, Python 3.14.8, 1777 test ID / 176 модулей на ОС, 3508 conformance-сценариев и 160 программ корпуса. Смешанные tuple и результаты циклов, общий heap и сохранённые codecs. Внешняя сверка TradingView остаётся отдельным этапом.


Карта ограниченных профилей OpenPine, 2026-11-02. Это не процент совместимости.
[Приёмка ссылочных полей и графов](OBJECT_GRAPHS_ACCEPTANCE.md): CI 57/57,
1752 test ID, 3456 conformance-сценариев и 152 программы корпуса (146 исполнений / 6 отказов).
Записи справочника, принятые правила, выполненные программы и внешние наблюдения
TradingView — разные свидетельства. Внешнее сравнение остаётся отдельным этапом.

**Ч** — реализован частичный профиль; **Р** — реализованный профиль с явно
реконструированными историческими деталями; **—** — конструкция не допускается
в этой версии; **нет** — реализация отсутствует. «Нет» не означает отсутствия
возможности в самом Pine. Ссылки задают точные сигнатуры и ограничения.

| Область | v1 | v2 | v3 | v4 | v5 | v6 | Граница / свидетельство |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Цветовые inputs | — | — | — | Ч | Ч | Ч | [Visual bands](VISUAL_BANDS.md); preset/override, frozen RGBA |
| Plot/hline references и basic fill | Р | Р | Р | Ч | Ч | Ч | [Visual bands](VISUAL_BANDS.md); только direct global immutable handles, same-kind endpoints |
| Раскраска и сигнальные метки | Р | Р | Ч | Ч | Ч | Ч | [Visual signals](VISUAL_SIGNALS.md); bgcolor/barcolor/plotshape/plotchar, static offset и два RGBA канала |
| Переменные, scalar-типы, `:=`, `var`, арифметика | Р | Р | Ч | Ч | Ч | Ч | [Statements](STATEMENTS.md), [series](SERIES.md); не вся система типов |
| `if`, `?:`, логика, qualifiers | Р | Р | Ч | Ч | Ч | Ч | [Functions](FUNCTIONS.md); numeric/strict до v6, bool/lazy в v6 |
| UDF, захваты, состояние написанных вызовов | Р | Р | Р | Р | Ч | Ч | [Functions](FUNCTIONS.md); без рекурсии; старые named arguments реконструированы |
| Перегрузки UDF | — | — | — | — | Ч | Ч | [Overloads](FUNCTION_OVERLOADS.md); total parameters v5 / required parameters v6; Binder выбирает конкретный ID |
| Методы scalar и primitive arrays | — | — | — | — | Ч | Ч | [Methods](METHODS.md); точный receiver, instance sugar, закрытый выбор и прежнее состояние |
| Литеральные defaults UDF | — | — | — | — | Ч | Ч | [Новый профиль](SEMANTIC_CORE.md), `test_semantic_core.py`; общие default-выражения исключены |
| `switch` по selector и условиям | — | — | — | — | Ч | Ч | [Новый профиль](SEMANTIC_CORE.md); первый match, selector один раз, до 32 случаев |
| Плоские tuple-результаты, распаковка | Р | Р | Ч | Ч | Ч | Ч | [Composite](COMPOSITE.md); 2–16 scalar-компонентов |
| Примитивные массивы, алиасы, история ссылок | — | — | — | Ч | Ч | Ч | [Arrays](ARRAYS.md); закрытый реестр операций |
| Редактирование и поиск в массивах | — | — | — | Ч | Ч | Ч | [Array operations](ARRAY_OPERATIONS.md); insert/remove/reverse/fill, поиск, concat; enum/UDT с v5 |
| Сортировка, ранги и бинарный поиск | — | — | — | Ч | Ч | Ч | [Array order](ARRAY_ORDER.md); sort v4+, indices/search/nth v5+, UDT sort_field только v6 |
| Общие окна массивов `array.slice` | — | — | — | Р | Ч | Ч | [Array slice](ARRAY_SLICE.md); общий корень, фиксированные границы, COW и checkpoint; спорные края заданы профилем |
| `for` | — | Ч | Ч | Ч | Ч | Ч | [Loops](LOOPS.md); бюджеты итераций |
| `while` | — | — | — | — | Ч | Ч | [Loops](LOOPS.md) |
| `for…in` по массивам | — | — | — | — | Ч | Ч | [Arrays](ARRAYS.md); элемент / индекс+элемент |
| OHLCV, `[]`, `na/nz`, SMA/EMA/change/cross | Р | Р | Ч | Ч | Ч | Ч | [Series](SERIES.md), [versions](VERSIONS.md); EMA seed различается |
| Дополнительные TA и составные индикаторы | Р | Р | Ч | Ч | Ч | Ч | [Technical](TECHNICAL.md), [composite](COMPOSITE.md), [signals](SIGNALS.md); только включённые ядра |
| Динамические длины выбранных окон | Р | Р | Ч | Ч | Ч | Ч | [Windows](DYNAMIC_WINDOWS.md), [signals](DYNAMIC_SIGNALS.md); не все length-параметры |
| Математика, строки, scalar-преобразования | Р | Р | Ч | Ч | Ч | Ч | [Numeric](NUMERIC_TARGETS.md), [scalar](SCALAR_TARGETS.md); закрытые реестры |
| Input и параметры запуска | Р | Р | Ч | Ч | Ч | Ч | [Inputs](INPUTS.md); параметры версионируются отдельно |
| `security` / `request.security` | Р | Р | Ч | Ч | Ч | Ч | [Contexts](CONTEXT_GRAPH.md), [environment](REQUEST_ENVIRONMENT.md); numeric/bool/flat tuples, без nested requests/mutable captures |
| Вычисляемые request-цели и чистые UDF | Р | Р | Ч | Ч | Ч | Ч | [Function targets](FUNCTION_TARGETS.md); resolver до получения данных |
| `request.security_lower_tf` | — | — | — | — | Ч | Ч | [Lower TF](LOWER_TF.md), [events](LOWER_EVENTS.md); конкретный array-профиль |
| UTC/24×7 календарь, время, metadata | Р | Р | Ч | Ч | Ч | Ч | [Environment](ENVIRONMENT.md); timestamp с v2, time_close с v4 |
| Replay, rollback, расчёт на обновлениях | Р | Ч | Ч | Ч | Ч | Ч | [Intrabar](INTRABAR.md); модель записанных событий |
| `varip` скаляров и primitive arrays | — | — | — | Ч | Ч | Ч | [Intrabar](INTRABAR.md); расширяет исходный array-профиль |
| Стратегии, заявки, fills, комиссии, риск | Р | Р | Ч | Ч | Ч | Ч | [Strategies](STRATEGIES.md), отдельные broker-профили; не весь strategy API |
| Пользовательские типы, поля, new/copy, UDT-методы | — | — | — | — | Ч | Ч | [Objects](OBJECTS.md); плоские scalar-поля, общий heap, история и field-level varip |
| Массивы плоских UDT | — | — | — | — | Ч | Ч | [Object arrays](OBJECT_ARRAYS.md); nominal elements, shallow copy, общий GC, varip структуры и отдельных полей |
| Enums и `input.enum` | — | — | — | — | Ч | Ч | [Enums](ENUMS.md); nominal scalar, UDF/методы, UDT-поля, массивы, история и varip; без enum-dependent requests |
| Типизированные maps и for-in по ключам/значениям | — | — | — | — | Ч | Ч | [Maps](MAPS.md); primitive/enum keys, scalar/enum/flat UDT values, общий heap и checkpoint |
| Матрицы, форма и for-in по строкам | — | — | — | — | Ч | Ч | [Matrices](MATRICES.md); primitive/enum/flat UDT, общий heap, shallow копии и checkpoint; без linear algebra |
| Ссылочные поля UDT и рекурсивные графы | — | — | — | — | Ч | Ч | [Object graphs](OBJECT_GRAPHS.md); UDT/array/map/matrix поля, циклы, GC, shallow copy и rollback |
| Смешанные tuple и расширенные результаты циклов | Р | Р | Ч | Ч | Ч | Ч | [Product results](PRODUCT_RESULTS.md); arrays с v4, enum/UDT/map/matrix с v5; for с v2 |
| Прямые вложенные коллекции | — | — | — | — | — | — | Профиль явно отклоняет; косвенная вложенность через поля UDT поддержана с v5 |
| Цвет, операции и представление plot | Р | Р | Р | Ч | Ч | Ч | [Colors](COLORS.md); разные палитры v5/v6, история, коллекции, Parquet и график; прежние version gates |
| Изменяемые метки `label` | — | — | — | Ч | Ч | Ч | [Labels](LABELS.md). Четыре операции, подтверждённый журнал и rollback |
| Оформление ценовых меток | — | — | — | Ч | Ч | Ч | [Оформление](LABEL_STYLES.md). Шесть форм, RGBA, размеры `size.*` и целый размер в v6 |
| Привязка меток к свечам | — | — | — | Ч | Ч | Ч | [Режим Y](LABEL_YLOC.md). Три режима, `set_yloc`, `set_y` и экстремумы открытой свечи |
| Изменяемые отрезки `line` | — | — | — | Ч | Ч | Ч | [Lines](LINES.md). Две точки, четыре операции, rollback и пересечение окна графика |
| Продление линий и `line.get_price` | — | — | — | Ч | Ч | Ч | [Line extensions](LINE_EXTENSIONS.md). Четыре режима, экстраполяция, replay и обрезка лучей |
| Оформление линий | — | — | — | Ч | Ч | Ч | [Оформление](LINE_STYLES.md). Цвет, шесть стилей, ширина 1–64, журнал и replay |
| Заливки между линиями `linefill` | — | — | — | — | Ч | Ч | [Linefills](LINEFILLS.md). Пять операций, живые границы, RGBA, checkpoint и replay |
| Временные координаты рисунков | — | — | — | Ч | Ч | Ч | [Координаты](DRAWING_XLOC.md). Именованный `xloc` линий, меток и зон, одна ось и отдельный preview |
| Смена xloc линии и метки, геттеры X линии, метки и бокса | — | — | — | Ч | Ч | Ч | [Смена режима](DRAWING_XLOC_MUTATIONS.md). Одно обновление, исходные X и откат preview |
| Смена xloc бокса | — | — | — | — | — | Ч | [Смена режима](DRAWING_XLOC_MUTATIONS.md). В v4–v5 действует `BOX_SET_XLOC_VERSION` |
| Точки `chart.point` | — | — | — | — | Ч | Ч | [Точки](CHART_POINTS.md). Алиасы, копия, точечные конструкторы и сеттеры линий, меток и зон |
| Ломаные `polyline` и массивы точек | — | — | — | — | Ч | Ч | [Ломаные](POLYLINES.md). Прямые сегменты, замкнутая заливка, снимок вершин, журнал и replay |
| Ценовые зоны `box` | — | — | — | Ч | Ч | Ч | [Boxes](BOXES.md). Индексы баров, RGBA, rollback и обрезка рамки |
| Оформление и продление рамок боксов | — | — | — | Ч | Ч | Ч | [Рамки](BOX_BORDERS.md). Три стиля, ширина 1–64, продление горизонтальных сторон |
| Закреплённые таблицы `table` | — | — | — | Ч | Ч | Ч | [Таблицы](TABLES.md). Четыре операции, девять позиций, RGBA, checkpoint и replay |
| Pine libraries: import/export | нет | нет | нет | нет | нет | нет | Linker библиотек отсутствует |

Новый профиль: `spec/semantic-core-profile.json`. Независимые ожидания:
`conformance/semantic-core/cases.json` — 22 случая: 12 исполнений и 10 отказов.
Старые conformance-источники и bundles сохраняются. Unit-тест отказа defaults
обновлён на границу v1–v4 / исполнение v5–v6.

На этапе сортировки/поиска корпус содержал 120 программ: 114 исполнений и прежние 6 отказов.
Сохранены все 112 записей предыдущего этапа; добавлены `array-order-routing-v1`…`v6`
и `array-order-strategy-v5/v6`. Новый conformance: 138 случаев (68 исполнений / 70 отказов).
Три прежних v4/v5/v6-uncovered-sort сохраняют источники и ID, но переходят от отказа к исполнению.
На этапе расширенных операций массивов корпус содержал 112 программ: 106 исполнений / 6 отказов.
Сохранены все 104 записи предыдущего этапа; добавлены `array-ops-routing-v1`…`v6`
и `array-ops-strategy-v5/v6`. Новый conformance: 127 случаев (60 исполнений / 67 отказов).
V1–v3 используют scalar-аналоги; операции массивов доступны с v4.
На этапе enum были добавлены следующие восемь программ.
Сохранены все 96 записей предыдущего этапа; добавлены `enum-routing-v1`…`v6`
и `enum-strategy-v5/v6`. Enum-conformance содержит 82 случая: 43 исполнения и
39 отказов. В routing v1–v4 используются числовые настройки, в v5/v6 — enum inputs.
На этапе массивов объектов были добавлены `object-queue-routing-v1`…`v6`
и `object-queue-strategy-v5/v6`. В v1–v3 routing использует scalar-аналоги,
в v4 primitive arrays. Object-arrays-corpus содержит 68 случаев: 40 исполнений
и 28 отказов. Objects-corpus сохраняет 66 ID: 30 исполнений и 36 отказов после
двух явных переходов от отказа к исполнению `array<T>`. Это состав корпуса; результаты
конкретных прогонов фиксируются в приёмке отдельно.

Каждое расширение требует версионного правила, ручного ожидания, диагностики
неподходящей версии и проверки через IR/runtime, worker, history/replay и
восстановление. Успех parser не закрывает строку таблицы. Приоритет следующей
порции определяется блокерами целых программ и нужными builtins;
далее необходимые блокерам builtins и расширения типов. Неявного fallback с v1–v5 на v6 нет.
