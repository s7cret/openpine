# OpenPine Stage 2 — repaired candidate, 18.09.2026

**Исправленная полная поставка восьми компонентов. Это не Stage 2 Accepted.**

```
status = in_progress
full_stage2_accepted = false
```

Основа: `OpenPine_Stage2_Sealed_Candidate_2026-09-17 (1).zip`.
Изменения относятся к этой поставке, а не к неизвестному текущему GitHub HEAD.

## Содержимое

- `sources/` — полные исходники всех восьми компонентов, включая исправления и тесты.
- `REPAIR_REPORT.md` — статус каждого из 25 замечаний, результаты и ограничения.
- `repair-evidence/` — текущие и промежуточные логи/JUnit, рассмотренные изменения и исходный аудит.
- `DELIVERY_MANIFEST.json` — хеш каждого поставляемого файла.
- `sources/openpine/verification/stage2-source-lock.json` — одно дерево всех восьми компонентов.
- `sources/openpine/verification/stage2-current-acceptance.json` — единственный текущий статус приёмки.

## Проверка поставки

```bash
python verify_sources.py
python3.13 run_repair_checks.py --suite targeted
python3.11 run_repair_checks.py --suite full
```

Для тестов нужны зависимости, объявленные компонентами, включая pytest, PyPA build
и backend сборки. Скрипт ничего не скачивает и не подменяет отсутствующие библиотеки.
`--suite full` запускает три полных языковых suite и выбранные host gates;
это **не** полный eight-component/worker/frontend DoD. Запуск из обоих Python
должен быть выполнен в подготовленном окружении. Результаты сохраняются в
`local-check-results/` и не входят в неизменяемую поставку.

## Незакрытые пункты

S2-13: полная историческая контрактная authority, включая CAT-04.
S2-14: mixed-version imports; same-version проверки сохранены, версия библиотеки не переписывается.
S2-15: полный независимый builtin oracle и обязательные execution paths.
S2-23: совместный DoD Python 3.11/3.13, protected workers, Stage 1, frontend и всех сборок.

`UNVERIFIED`, отсутствие receipts и зависимостей не заменены зелёным статусом.
Старые `delivery-evidence/` и помеченные исторические документы сохранены, но
не считаются результатом проверки исправленного дерева.
