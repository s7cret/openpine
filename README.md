[Единая память R6](docs/UNIFIED_STORAGE.md): SMA/change/`[]`, типизированный учёт и общий бюджет задания. [Приёмка](docs/UNIFIED_STORAGE_ACCEPTANCE.md): 1247 тестов, 2419 прежних наблюдений, два процесса, 10 000 020 сохранённых значений, CI 53/53.

[RAM и восстановление R6](docs/LIVE_RECOVERY_ACCEPTANCE.md): очередь по свободной памяти, компактные и блочные checkpoint, realtime v1–v6; 1264 теста на каждой ОС и CI 53/53.

[Хранение истории](docs/history-storage.md): доказанные границы окон, постепенное выделение памяти и общий бюджет контекстов. [Приёмка](docs/HISTORY_STORAGE_ACCEPTANCE.md): 1230 тестов, 2419 прежних наблюдений, 1 000 002 события, CI 53/53.

# OpenPine R6

[Динамические сигналы](docs/DYNAMIC_SIGNALS.md): VWMA, MOM/ROC, тренд и смещения экстремумов с меняющейся длиной v1–v6. [Приёмка](docs/DYNAMIC_SIGNALS_PLAN.md): 1207 тестов, 2419 наблюдений, миллион событий и CI 53/53.

[Динамические окна](docs/DYNAMIC_WINDOWS.md): меняющаяся длина TA-окон v1–v6, ограниченная история и восстановление состояния. [Приёмка](docs/DYNAMIC_WINDOWS_PLAN.md): 1182 теста, 2371 наблюдение, миллион событий и CI 51/51.

[Базовая математика и числовые цели](docs/NUMERIC_TARGETS.md): общий реестр числовых ядер, восемь новых функций v1–v6, чистые цели и формулы запрошенных рынков. [Приёмка](docs/NUMERIC_TARGETS_PLAN.md): 1154 теста, 2309 наблюдений, миллион событий и CI 49/49.

[Строки и scalar-цели](docs/SCALAR_TARGETS.md): общий ограниченный реестр строковых функций и преобразований интервалов для runtime и чистых целей запросов, с доступностью по v1–v6. [Приёмка](docs/SCALAR_TARGETS_PLAN.md): 1128 тестов, 2251 наблюдение, миллион событий и CI 47/47.

[Чистые функции в целях запросов](docs/FUNCTION_TARGETS.md): UDF, immutable locals и lexical captures в целях symbol/timeframe v1–v6; общий bounded resolver до получения данных. [Приёмка](docs/FUNCTION_TARGETS_PLAN.md): 1105 теста, 2214 наблюдений, миллион событий и CI 47/47.

[Цели относительно основного контекста](docs/PRIMARY_TARGETS.md): один IR, ссылки tickerid/period и пустые цели для v1–v6; metadata-only планирование и единый граф запуска. [Приёмка](docs/PRIMARY_TARGETS_PLAN.md): 1084 теста, 2176 conformance-наблюдений, миллион событий и CI 47/47.

[Вычисляемые цели запросов](docs/COMPUTED_TARGETS.md): const/input-условия и конкатенация v1–v6, специализированные inputs v5/v6, закреплённый граф до загрузки истории. [Приёмка](docs/COMPUTED_TARGETS_PLAN.md): 1066 тестов, 2138 conformance-наблюдений, миллион событий и CI 47/47.

[Подготовка и приёмка](docs/PREPARATION.md): кеш проверенной спецификации, переиспользование IR вариантов и устойчивые POST-чтения. [Результаты](docs/PREPARATION_PLAN.md): 1046 тестов, 2116 conformance-сценариев, миллион событий, CI 47/47; подготовка 256 вариантов 36,2 → 12,7 с в cProfile.

[Параметризуемые контексты](docs/PREPARED_REQUESTS.md): один сохранённый IR, закреплённые inputs и отдельный граф снимков каждого запуска/варианта. [Приёмка](docs/PREPARED_REQUESTS_PLAN.md): 1033 теста, 2116 conformance-сценариев, миллион событий и CI 41/41 на Python 3.14.8.

[Младшие свечи в replay и live](docs/LOWER_EVENTS.md): v5/v6, закреплённый прогрев, закрытые массивы, опоздания и совместное восстановление. [Приёмка](docs/LOWER_EVENTS_PLAN.md): 1018 тестов, миллион событий и CI 41/41 на Python 3.14.8.

[Массивы младших свечей](docs/LOWER_TF.md): `request.security_lower_tf` v5/v6, собственные выражения и состояния контекста, общие массивы и восстановление. [Приёмка закрыта](docs/LOWER_TF_PLAN.md): 1005 тестов, 2096 conformance-сценариев, миллион младших свечей и CI 41/41 на Python 3.14.8.

[Исполнение по младшим свечам](docs/BAR_MAGNIFIER.md): отдельные неизменяемые входы, явная модель цены, общее исполнение заявок и восстановление внутри основного бара для v1–v6. [Приёмка закрыта](docs/BAR_MAGNIFIER_PLAN.md): 984 теста, 2074 сохранённых conformance-наблюдения, миллион модельных точек и CI 41/41 на Python 3.14.8.

[Фазы стратегии](docs/STRATEGY_CYCLE.md): пересчёт после исполнений, обработка заявок на закрытии, отдельные счётчики и атомарное восстановление v1–v6. [Приёмка закрыта](docs/STRATEGY_CYCLE_PLAN.md): 966 тестов, 2074 сохранённых conformance-наблюдения, миллион событий и CI 39/39 на Python 3.14.8.

[Расчёт Pine на обновлениях](docs/INTRABAR.md): версионный `calc_on_every_tick`, `varip`, команды внутри свечи и восстановление. [Приёмка закрыта](docs/INTRABAR_PLAN.md): 933 теста, 2074 conformance-наблюдения, миллион обновлений и CI 37/37 на Python 3.14.8.

[Заявки на live/replay v1–v6](docs/OBSERVED_TRADING.md): общий брокер по наблюдаемым ценам, защита и trailing внутри свечи, сохранённые исполнения и офлайн-запись. [Приёмка закрыта](docs/OBSERVED_TRADING_PLAN.md): 909 тестов, 2074 прежних conformance-наблюдения, миллион событий и CI 35/35 на Python 3.14.8.

[Устойчивые запросы и исследования](docs/RESILIENCE.md): ограниченные повторы чтений, атомарные квитанции создания, восстановление отправки и общий wheel в CI. [Приёмка и доказательства](docs/RESILIENCE_PLAN.md).

[Статистика и история сделок](docs/PERFORMANCE.md): восемь переменных v1–v6, числовые методы v5/v6, дисковый индекс, частичные закрытия и восстановление. [Приёмка закрыта](docs/PERFORMANCE_PLAN.md): 855 тестов, 2074 conformance-примера, CI 29/29 на Python 3.14.8, миллион свечей и 250 тысяч закрытых частей.

[Управление риском](docs/RISK.md): шесть `strategy.risk.*` v1–v6, лимиты входов, просадки и дневной торговли, отмена и закрытие по риску. [Приёмка закрыта](docs/RISK_PLAN.md): 833 теста, 2024 conformance-примера, CI 27/27 на Python 3.14.8, миллион свечей и восстановление.

[Размер позиции и комиссии](docs/ECONOMICS.md): fixed/cash/percent of equity, явный qty, закреплённые ограничения и три типа комиссии v1–v6. [Приёмка](docs/ECONOMICS_PLAN.md): 802 теста, 1928 conformance-сценариев, миллион свечей и CI 25/25.

[Относительные выходы и trailing](docs/TRAILING.md): `profit/loss`, активация и подтягивание отдельно для каждого лота v1–v6, текущий/следующий стоп и восстановление. [Приёмка](docs/TRAILING_PLAN.md): 776 тестов, 1807 conformance-сценариев, миллион свечей и CI 23/23.

[Связанные заявки и netting](docs/ORDER_GROUPS.md): `strategy.order`, `close_all`, OCA none/cancel/reduce для v1–v6, точные остатки и восстановление. [Приёмка](docs/ORDER_GROUPS_PLAN.md): 741 тест, 1645 conformance-сценариев, миллион свечей и CI 21/21.

[Лоты и pyramiding](docs/LOTS.md): несколько входов, FIFO/ANY, точные остатки и комиссии, восстановление и состав позиции на графике для v1–v6. [Приёмка](docs/LOTS_PLAN.md): 714 тестов, 1459 conformance-сценариев, миллион свечей и CI 9/9.

[Отложенные входы](docs/PENDING_ENTRIES.md): limit, stop, stop-limit v1–v6, отдельная активация, точные targets частичной защиты и восстановление. [Приёмка](docs/PENDING_ENTRIES_PLAN.md): 690 тестов, 1309 conformance-сценариев, миллион свечей и CI 9/9.

[Частичные выходы](docs/PARTIAL_EXITS.md): qty/qty_percent, несколько защитных выходов, резервирование остатка и распределение комиссии для v1–v6. [Приёмка](docs/PARTIAL_EXITS_PLAN.md): 663 теста, 1189 conformance-сценариев, миллион свечей и CI 9/9.

[Вычисления внутри security](docs/CONTEXT_GRAPH.md): граф неизменяемых зависимостей, история выражений, UDF с отдельным состоянием и смешанные числовые/bool tuple для v1–v6. [Приёмка](docs/CONTEXT_GRAPH_PLAN.md): 639 тестов, 1105 conformance-сценариев, миллион свечей и CI 9/9.

[События и взвешенные окна](docs/SIGNALS.md): valuewhen/barssince, cum/sum, WMA/VWMA, momentum, проверки тренда и смещения экстремумов для v1–v6. [Приёмка](docs/SIGNALS_PLAN.md): 620 тестов, 1015 conformance-сценариев, миллион свечей и CI 9/9.

[Время и окружение](docs/ENVIRONMENT.md): UTC-дата, time/time_close, barstate, syminfo/timeframe, закреплённые метаданные и шесть стратегий с защитой в тиках. [Приёмка](docs/ENVIRONMENT_PLAN.md): 594 теста, 887 conformance-сценариев, миллион свечей и CI 9/9.

[Массивы и ссылки](docs/ARRAYS.md): массивы примитивов v4–v6, UDF, история, for…in, checkpoint и rollback.
[Приёмка массивов](docs/ARRAYS_PLAN.md): 569 тестов, 806 conformance-сценариев, миллион свечей и CI 9/9.

[Циклы и управление](docs/LOOPS.md): `for` с v2, `while` с v5,
версионные границы, `break`/`continue`, значения, состояние и бюджеты работы.
[Приёмка циклов](docs/LOOPS_PLAN.md): 527 тестов, 715 conformance-сценариев,
миллион свечей, восстановление и девять успешных CI jobs.

[Составные результаты и индикаторы v1–v6](docs/COMPOSITE.md): распаковка,
variance/stdev, MACD/BB, tuple-запросы и 12 новых стратегий.

OpenPine R6: компилятор Pine v1–v6, историческое исполнение, изолированные задания и локальный веб-интерфейс. Все модули продукта находятся в одном репозитории.

[Практические индикаторы и стратегии v1–v6](docs/TECHNICAL.md): RMA/TR/ATR,
RSI, ценовые каналы, числовые выражения в старшем контексте и 18 примеров.
[Матрица возможностей и ограничений](docs/CAPABILITIES.md); офлайн-инвентаризация:
`python -m tools.capabilities --json`.

[Защитные выходы v1–v6](docs/PROTECTION.md): stop/limit, изменение и отмена,
явная OHLC-модель, восстановление, график и исследования.

[Исследовательский цикл](docs/RESEARCH.md): отчёты, описательные метрики,
управление через CLI при работающем сервере, удаление истории и walk-forward.

[Подбор параметров и отложенная проверка](docs/SEARCHES.md): конечная сетка
или случайный поиск с seed, закреплённый победитель и повтор сохранённых планов.

[Параметры `input`, пресеты и сравнение экспериментов](docs/INPUTS.md): одна
компиляция, независимые настройки, общая очередь и восстановление серии.

Быстрый старт на Python **3.14.8**: `python -m pip install .`, затем
`openpine serve`. Откройте напечатанный локальный адрес: загрузка `.pine`,
исторические данные из файла или Binance Spot, компиляция, отдельный worker,
графики и диагностика. Для разработки можно запускать `python -m apps.openpine serve`.
Подробности: [установка и весь путь](docs/PIPELINE.md), [этапы и приёмка](docs/PIPELINE_PLAN.md).
Веб определяет дополнительные контексты после компиляции и сохраняет
[подготовку запуска](docs/LAUNCH.md): файлы/снимки/провайдер, покрытие,
прогрев, исследования, продолжение после перезапуска и повтор результата.
[Приёмка подготовки](docs/LAUNCH_PLAN.md) закрыта: 495 тестов, 623 conformance,
миллион основных свечей и 250 тысяч старших, девять успешных CI jobs.

Хранение и задания: **SQLite 3.54.1**, **DuckDB 2.0.0.dev2610192346**
(закреплённая предварительная сборка), **Parquet/ZSTD**. Свечи и IR общие для
повторных запусков, выходы пишутся порциями, веб получает окна графика.
[Контракты, миграция JSON, ресурсы, команды и резервные копии](docs/STORAGE.md).
[Стабилизация R6: RunPlan, совместимость, лимиты, кеш диапазонов и обслуживание](docs/STABILIZATION.md).
[Единый исторический цикл, checkpoint, пауза и продолжение новым процессом](docs/CHECKPOINTS.md).
[Контексты v1–v6: снимки, security/request.security, числовые выражения и совместное восстановление](docs/CONTEXTS.md).
[Исторические стратегии v1–v6: market entry/close, позиции, сделки, капитал и совместное восстановление](docs/STRATEGIES.md).
[Офлайн replay v1–v6: обновления текущей свечи, rollback, общий журнал и восстановление](docs/REALTIME.md).
[Market data: каталог, фоновые загрузки, отчёт о покрытии, live Binance Spot, восстановление и веб-панель](docs/MARKETDATA.md).
[Длительные сессии: общий UTC-календарь, несколько потоков, переносимые записи, ротация и приёмка](docs/SESSIONS.md).

Единственная поддерживаемая ветка Python — **3.14** (`>=3.14,<3.15`). Для разработки и тестирования закреплена **Python 3.14.8** в `.python-version`.

При добавлении конфигурации дистрибутива `requires-python` должен быть `>=3.14,<3.15`. Общий gate и CI выполняются на Python 3.14.8.

Подготовлены [локальная спецификация Pine v1–v6](spec/README.md) и [сквозной запуск](docs/FIRST_EXECUTION.md): Pine → parser → binding → типизированный IR → runtime → результат `plot`. Во всех шести версиях работают переменные с выводом/аннотацией типа, арифметика, переприсваивание `:=`, сравнения, `?:`, `if/else`, `var`, абсолютная величина, `close` и EMA. [Правила конструкций](docs/STATEMENTS.md) закреплены отдельным профилем языка, [историческое исполнение](docs/HISTORICAL_EXECUTION.md) использует локальные подтверждённые бары.

Запуск из корня: `python -m apps.openpine run --source conformance/literal-harness/abs-float.pine` → значение `2.5` в `result.outputs[0].values`.

EMA на четырёх собственных синтетических барах:

```sh
python -m apps.openpine run --source conformance/historical-bars/ema.pine --bars conformance/historical-bars/fixed.json
```

Результат v5/v6: `[10.0, 15.0, 22.5, 31.25]`. В v1–v4 используются `study`, `abs`, `ema`; в v5/v6 — `indicator`, `math.abs`, `ta.ema`. Без аннотации выбирается v1.

Сейчас критерий готовности — поведение нашей модели по закреплённым артефактам и независимым ожиданиям. Точное сравнение с TradingView запланировано отдельным этапом. Версионные решения и реконструированные детали сохранены в [VERSIONS.md](docs/VERSIONS.md).

Пример со всеми пятью конструкциями: `python -m apps.openpine run --source conformance/language/v6-five-points.pine --bars conformance/language/fixed.json`. Для старых версий есть соответствующие `v1-…` … `v5-…` программы; общий gate проверяет каждую версию.

Во всех версиях добавлены [OHLCV, история `[]`, `na/nz`, числовые операции и SMA/change/cross](docs/SERIES.md). Общий пример: `python -m apps.openpine run --source conformance/series/v6-combined.pine --bars conformance/series/ohlcv.json`. Для v1–v5 есть отдельные программы с соответствующими именами API.

Во всех версиях работают [пользовательские функции `=>`, возвращающие блоки и независимое состояние вызовов](docs/FUNCTIONS.md). Полный пример: `python -m apps.openpine run --source conformance/functions/v6-combined.pine --bars conformance/functions/fixed.json`.

Подготовка: `python -m pip install -r requirements-dev.txt`. Общая проверка: `python -m tools.release_gate`. После установки зависимостей проверка спецификации и исполнение работают офлайн. Файлы `.gitkeep` сохраняют пока пустые каталоги в Git.

```text
openpine/
├── spec/
├── packages/
│   ├── contracts/
│   ├── compiler/
│   │   ├── frontend/
│   │   ├── semantic/
│   │   ├── ir/
│   │   ├── lowering/
│   │   └── codegen/
│   ├── runtime/
│   │   ├── types/
│   │   ├── series/
│   │   ├── state/
│   │   ├── operations/
│   │   ├── requests/
│   │   └── outputs/
│   ├── execution/
│   │   ├── session/
│   │   ├── scheduling/
│   │   ├── profiles/
│   │   └── checkpoints/
│   ├── simulation/
│   │   ├── broker/
│   │   ├── orders/
│   │   ├── fills/
│   │   └── ledger/
│   ├── marketdata/
│   ├── storage/
│   └── optimizer/
├── apps/
│   ├── openpine/
│   ├── worker/
│   └── ui/
├── conformance/
├── tests/
├── tools/
└── docs/
```
