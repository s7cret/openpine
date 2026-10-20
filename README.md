# OpenPine R6

Каркас единого монорепозитория OpenPine. Все модули продукта находятся в этом репозитории и развиваются совместно.

Единственная поддерживаемая ветка Python — **3.14** (`>=3.14,<3.15`). Для разработки и тестирования закреплена **Python 3.14.8** в `.python-version`.

При добавлении конфигурации пакета `requires-python` должен быть `>=3.14,<3.15`, а CI должен запускать проверки на Python 3.14.8.

На этом этапе созданы только каталоги. Реализация, конфигурация сборки, зависимости, CI и тесты будут добавляться постепенно вместе с выбранными возможностями. Файлы `.gitkeep` сохраняют пустые каталоги в Git.

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
