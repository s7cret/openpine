# OpenPine Stage 2 — исправленный кандидат от 18.09.2026

## Результат

Основа — точный приложенный `OpenPine_Stage2_Sealed_Candidate_2026-09-17 (1).zip`, а не GitHub HEAD. В поставке сохранены полные исходники всех восьми компонентов.

**Из 25 замечаний внесены исправления по 21 конкретному дефекту. Четыре требования — S2-13, S2-14, S2-15 и S2-23 — остаются незакрытыми.** Это не окончательное выполнение всего ТЗ этапа 2. Исправление ошибочного механизма приёмки не считается реализацией отсутствующей функциональности.

```text
status = in_progress
full_stage2_accepted = false
```

Текущий машиночитаемый статус: `sources/openpine/verification/stage2-current-acceptance.json`. Исторические матрицы и логи не являются текущими приёмочными свидетельствами.

## Статус всех замечаний

| ID | Статус | Что изменено / что осталось |
|---|---|---|
| S2-01 | Исправлено | `%` согласован с floor-частным: одинаковое поведение вычисления констант и runtime. Проверены знаки, числа, NA и нулевой делитель. |
| S2-02 | Исправлено | При вычислении констант `bool` не смешивается с числами через Python `False == 0`. Результаты согласованы с типизированным runtime. |
| S2-03 | Исправлено | Старое целочисленное деление не проходит через `float`; сохраняется точность больших целых, включая отрицательные операнды. |
| S2-04 | Исправлено | Сравнения непосредственно с литералом `na` отклоняются. Переменная с NA и вызов `na(value)` не запрещаются этим правилом. |
| S2-05 | Исправлено | Default поля UDT ограничен допустимой формой: литерал, поддерживаемая встроенная переменная или член enum. Вызовы, произвольные выражения и пользовательские глобальные переменные отклоняются. |
| S2-06 | Исправлено | Реализованы сквозные bindings `nz` и `color.r`, дополнительно g/b/t. `nz` учитывает тип результата, различает пропущенный replacement и явно переданный NA; не принимает Python null/NaN/Inf как корректное Pine-значение. |
| S2-07 | Исправлено | Исправлены сигнатуры `str.contains`, `substring`, `replace`, `replace_all`, `startswith`, `endswith`, `split`, `tostring` для современной поверхности v5/v6. Исправлен и генератор каталога. |
| S2-08 | Исправлено | Строковые вызовы передают конкретные типы аргументов и корректное сопоставление параметров. Обязательные consumer-инварианты не ослаблены. |
| S2-09 | Исправлено | Добавлены типизированные `array.from`/`array.sum` и ABI-связи; проверены variadic-аргументы, числовое продвижение, slices, NA, пустые массивы и ошибочные типы. |
| S2-10 | Исправлено | `str.tostring(enum)` получает title через nominal type registry. Проверены локальные и импортированные одноимённые enum, неправильные типы и неподдерживаемый format для enum. |
| S2-11 | Исправлен gate | Inventory integrity, контрактная полнота и независимая authority разделены. Каталог с обязательными UNVERIFIED больше не получает общий Accepted. Полнота самого каталога этим не объявлена. |
| S2-12 | Исправлен gate | Фактические параметры, defaults, qualifiers, overload и return type сверяются с независимым обязательным набором измерений. Добавлены mutation-тесты. |
| S2-13 | **Открыто** | Нет полного независимого исторического подтверждения всех version-exact контрактов; CAT-04 для v4 binary search не разрешён. Статусы UNVERIFIED сохранены. |
| S2-14 | **Открыто** | Импорты остаются same-version. Общий запрет нельзя безопасно заменить допуском без сохранения семантики версии библиотеки; mixed-version runtime и подтверждённая матрица не реализованы. |
| S2-15 | **Открыто** | Новые независимые буквальные expected покрывают исправленные функции, но полный builtin oracle и все обязательные execution paths не закрыты. STATE-03/04 сохраняются. |
| S2-16 | Исправлено | Callable lock рассмотрен и обновлён: 2390 строк сохранены. В review записаны прежние bool-изменения v1–v6 и новые строковые контракты v5/v6. Индекс больше не падает из-за drift lock. |
| S2-17 | Исправлено | Canonical hash remaining-matrix и связанная ссылка исправлены. Команда показывает настоящий незакрытый остаток, а не падает на hash mismatch. |
| S2-18 | Исправлено | Публикационные JSON включены в wheel и sdist Pine2AST/AST2Python. Проверены реальные архивы и загрузка из установленного wheel без source checkout. |
| S2-19 | Исправлено | Локальная проверка возвращает `verified_local` и перечень отсутствующих наблюдений. Coordinated требует все identities трёх компонентов. |
| S2-20 | Исправлено | Full=true требует закрытых residuals, четырёх accepted критериев и реальных чистых receipt-файлов на одном source lock. Проверяются пути, хеши и содержимое, а не только переданные флаги. |
| S2-21 | Исправлено | Legacy blacklist проверяет границы идентификатора: `target_version` по-прежнему запрещён, `compiler_target_version` не даёт ложного срабатывания. Защита сохранена и дополнена тестом. |
| S2-22 | Исправлено | Введён единственный current acceptance receipt. Прежние статусы и матрицы помечены историческими, исходные документы сохранены отдельно. Root verifier проверяет согласованность текущего статуса и дерева. |
| S2-23 | **Открыто** | Создан точный lock восьми компонентов и проверка целостности, но нет полного общего Python 3.11/3.13 + protected workers + Stage 1 + frontend + all-package CI. |
| S2-24 | Исправлен тест | Первое исполнение `once` происходит на неподтверждённом tick. Проверяются replay/commit и настоящее JSON checkpoint/restore. Отключённая строка `if False` удалена; отдельный fill-recalculation тест сохранён. |
| S2-25 | Исправлен тест | Проверяется настоящий tuple-result цикла, break/continue/zero iterations; старый scalar-assignment сценарий сохранён отдельно. Найден и устранён parser-дефект соседних tuple/loop-блоков после DEDENT. |

«Исправлено» означает устранение конкретного контрпримера и добавление соответствующих проверок, а не математическое доказательство полной Pine-совместимости подсистемы.

## Реальные результаты исполнения

Среда: Python **3.13.5**. Результаты ниже взяты из JUnit, а не из collect-only.

| Прогон | Passed | Failures | Errors | Skipped | Доказательство |
|---|---:|---:|---:|---:|---|
| PineLib, полный suite | 5079 | 0 | 0 | 0 | `repair-evidence/results/pinelib-full-complete.xml` |
| AST2Python, полный suite | 1783 | 0 | 0 | 0 | `repair-evidence/results/ast2python-full-final.xml` |
| Pine2AST, полный suite | 3431 | 0 | **3** | 0 | `repair-evidence/results/pine2ast-full-final.xml` |
| Выбранные host-модули | 309 | 0 | 0 | 0 | `repair-evidence/results/host-selected-final.xml` |
| Повторные foundation/inventory mutation checks | 60 | 0 | 0 | 0 | `repair-evidence/results/host-foundation-final.xml` |
| Сквозные repaired runtime/builtins | 117 | 0 | 0 | 0 | `repair-evidence/results/repaired-runtime.xml` |
| Новые direct-value негативные проверки | 27 | 0 | 0 | 0 | `repair-evidence/results/direct-value-regressions.xml` |

Поднаборы пересекаются с полными suite: **их нельзя складывать как число уникальных проверок**.

Три ошибки Pine2AST — setup errors `test_reference_package_data.py`: в среде отсутствует модуль PyPA `build`. Эти тесты не помечены skip/xfail и не удалены. Полный Pine2AST suite поэтому не объявляется зелёным. Ручная проверка настоящих backend-сборок и wheel-only установки выполнена отдельно и не подменяет отсутствующий pytest-прогон.

Поздние изменения затрагивали inventory review, исторические labels и delivery metadata. Они проверены отдельно; три полных suite не выдаются за один одновременный CI неизменяемого окончательного дерева. Финальная поставка имеет свой точный source lock.

### Сборки и установка

Реальные backend-вызовы собрали **wheel и sdist 7 из 8 компонентов**. Backtest_engine не собран: отсутствует `hatchling`. Отдельно для трёх языковых пакетов проверено:

- публикационный JSON присутствует в wheel и sdist и совпадает с исходным;
- после установки Python `-I` загружает пакеты из `site-packages`, не из checkout;
- coordinated publication подтверждает версии/catalog/compiler/runtime identities, сохраняя full=false.

Логи: `package-builds.json`, `build-*.log`, `package-resource-checks.json`, `wheel-only-publication.log`. Повторная сборка языковых wheel выполнена после последних изменений runtime и publication metadata. Это артефакты диагностических сборок, **не готовая автономная wheelhouse со всеми зависимостями**.

### Непройденные и недоступные проверки

Python 3.11 отсутствует. Нет рабочего protected-worker окружения с Bubblewrap; sandbox не отключался и in-process подмена не использовалась. Полный host collection ограничен отсутствующей `structlog`. Vitest не установлен; отдельные Node-тесты дали 11 passed / 9 failed из-за отсутствующей TypeScript/build-среды. Полный Stage 1 corpus и весь восьмикомпонентный DoD не подтверждены.

В ходе работы были ранние падения тестов на прежние ABI/catalog hash-ожидания и старые intentionally-unsupported builtin assertions. Они сохранены в `*-first.log/xml`. После рассмотренных исправлений полные PineLib и AST2Python проходят. Это не удаление прежних неудачных результатов.

## Inventory и неизменяемые контракты

| Компонент | Входной inventory | Текущий collect-only inventory |
|---|---:|---:|
| Pine2AST | 3420 | 3434 |
| AST2Python | 1685 | 1783 |
| PineLib | 5048 | 5079 |

Итого **143 дополнительных тестовых случая по трём библиотекам**. У host добавлены 17 mutation-проверок. Полный host inventory не собран: его прежний exact hash сохраняется, а новая схема допускает только эти 17 конкретных node IDs. При проверке они удаляются из фактического списка, после чего остаток должен точно совпасть с прежним count/hash. Неизвестные добавления, удаления и дубликаты отвергаются.

Три AST2Python теста, ранее ожидавшие отказ `nz`/color, заменены на независимые expected успешного исполнения; замена явно записана в `stage2-audit-inventory-review.json`. Старый scalar-loop тест сохранён отдельным тестом.

Исторические catalog/ABI snapshots не заменены новым фактическим результатом. Добавлен строго проверяемый обратимый delta-слой: он подтверждает ровно рассмотренные изменения, затем восстанавливает прежний снимок и его исходные hashes. Непересмотренные строки продолжают контролироваться. Полный набор 2390 callable rows не сокращался.

Файлы review:

```text
sources/openpine/verification/stage2-audit-callable-review.json
sources/openpine/verification/stage2-audit-inventory-review.json
sources/pine2ast/pine2ast/reference_catalog/stage2_audit_catalog_delta.json
sources/pinelib/pinelib/abi/stage2_audit_repair_delta_lock.json
```

## Каталог и oracle после исправлений

Catalog inventory integrity проходит, но общий catalog acceptance корректно остаётся false: сохранены 4324 UNVERIFIED version cells и 525 непроверенных контрактных измерений. Это не список 4324 отсутствующих функций: клетки включают версии и разные execution classes. Более строгая проверка измерений может выявлять пробелы, ранее формально считавшиеся представленными.

Builtin-index сохраняет 2390 строк и успешно сверяет reviewed lock. Для 100 обязательных group/path сочетаний в текущем delivery-evidence не подтверждены подходящие receipts. Это **NOT_RUN, а не 100 численных несовпадений**. Существующие исторические frozen corpora сохранены, но не объявлены текущим общим прогоном.

Architecture gate прошёл. Remaining gate исправен и сообщает оставшиеся blockers; ненулевой exit code для непринятого этапа ожидаем.

## Проверка архива

После распаковки перейти в `openpine/`:

```bash
python verify_sources.py
python3.13 run_repair_checks.py --suite targeted
```

Для повторения полных библиотечных тестов в подготовленной среде:

```bash
python3.11 run_repair_checks.py --suite full
python3.13 run_repair_checks.py --suite full
```

Нужны объявленные зависимости всех компонентов и pytest/build backend. Runner не устанавливает зависимости и не выдаёт полный DoD по трём библиотечным suite. Его `--suite full` означает три полных языковых suite плюс выбранные host gates. Результаты пишутся в `local-check-results/`, исключённый из неизменяемой поставки.

`verify_sources.py` проверяет каждый доставленный файл, точное дерево всех восьми компонентов, ссылки source lock/current receipt и согласованность full=false. Это проверка целостности, не сертификат корректности языка.

## Источники решений и ограничения

Нормативная основа: два приложенных ТЗ и исходный аудит, включённые в `repair-evidence/original-audit/`. Для конкретных семантических решений использовалась официальная документация TradingView, а не наблюдаемый output OpenPine:

- modulo: https://www.tradingview.com/pine-script-docs/language/operators/
- NA и UDT defaults: https://www.tradingview.com/pine-script-docs/language/type-system/
- enum titles: https://www.tradingview.com/pine-script-docs/language/enums/
- строки и массивы: https://www.tradingview.com/pine-script-docs/concepts/strings/ и https://www.tradingview.com/pine-script-docs/language/arrays/
- цвета и изменения v6: https://www.tradingview.com/pine-script-docs/visuals/colors/ и https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/

Литеральные expected новых тестов — ручные контрактные примеры. Они не называются TradingView exports и не закрывают всю historical authority. Performance не измерялась; ускорение не заявляется. GitHub не изменялся.

## Финальная проверка поставляемым runner

`python run_repair_checks.py --suite targeted` завершён успешно после согласования исходников: ast2python: 132 passed, openpine: 39 passed, pine2ast: 24 passed, pinelib: 43 passed. Всего 238 passed, 0 failures/errors/skips. Результаты находятся в `repair-evidence/results/delivery-targeted/`. Это целевой повторный прогон, не полный DoD.

Все 5534 входных файловых пути сохранены, включая заменённый корневой manifest. Проверка намеренной подмены файла дала отказ; после восстановления целостность подтверждена.
