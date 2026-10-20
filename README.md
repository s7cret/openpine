# OpenPine R6

Каркас единого монорепозитория OpenPine. Все модули продукта находятся в этом репозитории и развиваются совместно.

Единственная поддерживаемая ветка Python — **3.14** (`>=3.14,<3.15`). Для разработки и тестирования закреплена **Python 3.14.8** в `.python-version`.

При добавлении конфигурации дистрибутива `requires-python` должен быть `>=3.14,<3.15`. Общий gate и CI выполняются на Python 3.14.8.

Подготовлены [локальная спецификация Pine v1–v6](spec/README.md) и [сквозной запуск](docs/FIRST_EXECUTION.md): Pine → parser → binding → типизированный IR → runtime → результат `plot`. Ограниченный срез литералов, унарных знаков, абсолютной величины, `close` и EMA исполняется для всех шести версий через [отдельные версионные контракты](docs/VERSIONS.md). [Исторический профиль](docs/HISTORICAL_EXECUTION.md) выполняет программу по фиксированным подтверждённым барам из локального JSON.

Запуск из корня: `python -m apps.openpine run --source conformance/literal-harness/abs-float.pine` → значение `2.5` в `result.outputs[0].values`.

EMA на четырёх собственных синтетических барах:

```sh
python -m apps.openpine run --source conformance/historical-bars/ema.pine --bars conformance/historical-bars/fixed.json
```

Результат v5/v6: `[10.0, 15.0, 22.5, 31.25]`. В v1–v4 используются `study`, `abs`, `ema`; в v5/v6 — `indicator`, `math.abs`, `ta.ema`. Без аннотации выбирается v1.

**Исторические оговорки:** профили v1/v2 частично восстановлены, а EMA v3/v4 следует сохранённому примеру документации с SMA seed. Начальное состояние и NA-поведение старого движка TradingView независимо не подтверждены; эти ограничения присутствуют в `identity.compatibility_notes`. Поддержка текущего среза не означает полную совместимость Pine v1–v6. Подробности и матрица — в [VERSIONS.md](docs/VERSIONS.md).

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
