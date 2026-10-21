# OpenPine R6

OpenPine R6: компилятор Pine v1–v6, историческое исполнение, изолированные задания и локальный веб-интерфейс. Все модули продукта находятся в одном репозитории.

Быстрый старт на Python **3.14.8**: `python -m pip install .`, затем
`openpine serve`. Откройте напечатанный локальный адрес: загрузка `.pine`,
исторические данные из файла или Binance Spot, компиляция, отдельный worker,
графики и диагностика. Для разработки можно запускать `python -m apps.openpine serve`.
Подробности: [установка и весь путь](docs/PIPELINE.md), [этапы и приёмка](docs/PIPELINE_PLAN.md).

Хранение и задания: **SQLite 3.54.1**, **DuckDB 2.0.0.dev2610192346**
(закреплённая предварительная сборка), **Parquet/ZSTD**. Свечи и IR общие для
повторных запусков, выходы пишутся порциями, веб получает окна графика.
[Контракты, миграция JSON, ресурсы, команды и резервные копии](docs/STORAGE.md).
[Стабилизация R6: RunPlan, совместимость, лимиты, кеш диапазонов и обслуживание](docs/STABILIZATION.md).
[Единый исторический цикл, checkpoint, пауза и продолжение новым процессом](docs/CHECKPOINTS.md).
[Контексты v1–v6: снимки, security/request.security close/EMA и совместное восстановление](docs/CONTEXTS.md).
[Исторические стратегии v1–v6: market entry/close, позиции, сделки, капитал и совместное восстановление](docs/STRATEGIES.md).

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
