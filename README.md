# OpenPine R6

[Отложенные входы](docs/PENDING_ENTRIES.md): limit, stop, stop-limit v1–v6, отдельная активация, точные targets частичной защиты и восстановление. [План приёмки](docs/PENDING_ENTRIES_PLAN.md).

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
