# Реализованная семантика Pine v1–v6

Карта ограниченных профилей OpenPine, 2026-10-31. Это не процент совместимости.
[Приёмка массивов объектов](OBJECT_ARRAYS_ACCEPTANCE.md): CI 57/57,
1519 test ID, 2701 conformance-случай и 96 программ корпуса (90 исполнений / 6 отказов).
Записи справочника, принятые правила, выполненные программы и внешние наблюдения
TradingView — разные свидетельства. Внешнее сравнение остаётся отдельным этапом.

**Ч** — реализован частичный профиль; **Р** — реализованный профиль с явно
реконструированными историческими деталями; **—** — конструкция не допускается
в этой версии; **нет** — реализация отсутствует. «Нет» не означает отсутствия
возможности в самом Pine. Ссылки задают точные сигнатуры и ограничения.

| Область | v1 | v2 | v3 | v4 | v5 | v6 | Граница / свидетельство |
| --- | --- | --- | --- | --- | --- | --- | --- |
| Переменные, scalar-типы, `:=`, `var`, арифметика | Р | Р | Ч | Ч | Ч | Ч | [Statements](STATEMENTS.md), [series](SERIES.md); не вся система типов |
| `if`, `?:`, логика, qualifiers | Р | Р | Ч | Ч | Ч | Ч | [Functions](FUNCTIONS.md); numeric/strict до v6, bool/lazy в v6 |
| UDF, захваты, состояние написанных вызовов | Р | Р | Р | Р | Ч | Ч | [Functions](FUNCTIONS.md); без рекурсии; старые named arguments реконструированы |
| Перегрузки UDF | — | — | — | — | Ч | Ч | [Overloads](FUNCTION_OVERLOADS.md); total parameters v5 / required parameters v6; Binder выбирает конкретный ID |
| Методы scalar и primitive arrays | — | — | — | — | Ч | Ч | [Methods](METHODS.md); точный receiver, instance sugar, закрытый выбор и прежнее состояние |
| Литеральные defaults UDF | — | — | — | — | Ч | Ч | [Новый профиль](SEMANTIC_CORE.md), `test_semantic_core.py`; общие default-выражения исключены |
| `switch` по selector и условиям | — | — | — | — | Ч | Ч | [Новый профиль](SEMANTIC_CORE.md); первый match, selector один раз, до 32 случаев |
| Плоские tuple-результаты, распаковка | Р | Р | Ч | Ч | Ч | Ч | [Composite](COMPOSITE.md); 2–16 scalar-компонентов |
| Примитивные массивы, алиасы, история ссылок | — | — | — | Ч | Ч | Ч | [Arrays](ARRAYS.md); закрытый реестр операций |
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
| Maps, matrices, вложенные коллекции | нет | нет | нет | нет | нет | нет | Текущий типизированный IR их не допускает |
| Полная графика: color/line/label/box/table | нет | нет | нет | нет | нет | нет | Plot-harness не является полной графической моделью |
| Pine libraries: import/export | нет | нет | нет | нет | нет | нет | Linker библиотек отсутствует |

Новый профиль: `spec/semantic-core-profile.json`. Независимые ожидания:
`conformance/semantic-core/cases.json` — 22 случая: 12 исполнений и 10 отказов.
Старые conformance-источники и bundles сохраняются. Unit-тест отказа defaults
обновлён на границу v1–v4 / исполнение v5–v6.

Корпус содержит 104 программы: 98 исполнений и прежние 6 отказов.
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
