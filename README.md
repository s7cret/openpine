# OpenPine R6

Каркас единого монорепозитория OpenPine. Все модули продукта находятся в этом репозитории и развиваются совместно.

Единственная поддерживаемая ветка Python — **3.14** (`>=3.14,<3.15`). Для разработки и тестирования закреплена **Python 3.14.8** в `.python-version`.

При добавлении конфигурации дистрибутива `requires-python` должен быть `>=3.14,<3.15`. Общий gate и CI выполняются на Python 3.14.8.

Подготовлены [локальная спецификация Pine v1–v6](spec/README.md) и [первый работающий запуск](docs/FIRST_EXECUTION.md): ограниченный Pine v6 → parser → binding → типизированный IR → runtime → результат `plot`. Реализованы литералы, унарные знаки, `math.abs`, `close`, `ta.ema` и harness `indicator`/`plot`. [Исторический профиль](docs/HISTORICAL_EXECUTION.md) выполняет программу по фиксированным подтверждённым барам из локального JSON.

Запуск из корня: `python -m apps.openpine run --source conformance/literal-harness/abs-float.pine` → значение `2.5` в `result.outputs[0].values`.

EMA на четырёх собственных синтетических барах:

```sh
python -m apps.openpine run --source conformance/historical-bars/ema.pine --bars conformance/historical-bars/fixed.json
```

Результат: `[10.0, 15.0, 22.5, 31.25]`. Сейчас исполняется только явно проверенный срез v6. v1–v5 имеют отдельные справочные профили и диагностированный отказ исполнения; общей совместимости всех версий проект пока не заявляет.

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
