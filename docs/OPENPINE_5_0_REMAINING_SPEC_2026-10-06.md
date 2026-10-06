# OpenPine 5.0 — техническое задание на оставшиеся работы

**Дата актуализации:** 6 октября 2026 года, UTC+5.  
**Целевой результат:** полноценная согласованная версия продукта **OpenPine 5.0.0**.  
**Языковая область:** Pine v1–v6; номер продукта 5.0 не означает ограничение языком Pine v5.  
**Рабочая линия интеграции:** `release/5.0.0rc6` с последующей квалификацией финальной версии 5.0.0.  
**Область:** OpenPine, семь библиотек, `openpine-ui`, установленные пакеты и единая приёмка.

Документ задаёт только оставшуюся реализацию, интеграцию и квалификацию. Он не является журналом изменений или свидетельством принятия версии. Готовые механизмы используются через действующих владельцев; повторная реализация parser, runtime, broker, planner или acceptance engine не допускается.

## Содержание

1. [Область, правила и типы работ](#scope)
2. [Согласованная сборка и завершение тестовой платформы](#integration)
3. [Полная языковая область Pine v1–v6](#language)
4. [Данные, инструменты и request](#data)
5. [Lifecycle, полное восстановление и потоковый результат](#lifecycle)
6. [Стратегии и брокерская семантика](#broker)
7. [Производительность продукта и оптимизатор](#performance)
8. [Пользовательский интерфейс, visuals, alerts и диагностика](#ux)
9. [Установка, совместимость и выпуск 5.0.0](#delivery)
10. [Общая матрица проверок и правило приёмки](#acceptance)
11. [Порядок выполнения и передача результата](#execution)
12. [Трассировка исходных OP-01–OP-36](#traceability)
13. [Точный Git-срез и основания постановки](#sources)

<a id="scope"></a>
## 1. Область, правила и типы работ

### 1.1. Что должно получиться

Пользователь устанавливает версию 5.0 из пакетов, компилирует Pine с точными зависимостями, задаёт inputs, получает необходимые данные, запускает исторический или поддерживаемый realtime-расчёт, видит полный обещанный результат и диагностику. Прерванное задание восстанавливается через публичный интерфейс без потери состояния и повторных эффектов. Оптимизатор воспроизводит результаты trials и победителя. Все эти пути используют один согласованный комплект кода, контрактов, каталогов и настроек.

Версия не считается завершённой только по зелёному component CI, наличию команды, количеству тестов, упаковке ZIP или успешному merge. Неподтверждённая обязательная семантика остаётся незакрытым требованием; её нельзя переименовать в «поддерживается с ограничениями» без отдельного решения о составе продукта.

### 1.2. Типы оставшихся работ

| Метка | Смысл задания |
|---|---|
| **Д** — доработка | Требуется устранить конкретное ограничение или дополнить контракт/исполнение. |
| **И** — интеграция | Подходящий код опубликован отдельно; требуется согласовать и включить его в общий candidate, не реализовывать заново. |
| **К** — квалификация | Механизм или отдельные тесты существуют, но требуется полное применимое доказательство на поставляемом составе. Добавлять код только при выявленной недостаче. |

Метка **К** не означает, что функция отсутствует. Для такого пункта сначала связать имеющиеся тесты с обязательными случаями и выполнить их; затем добавить лишь отсутствующие сценарии и исправить реальные несоответствия. Наличие `not_accepted` в публикационном JSON само по себе не является доказательством отсутствия реализации.

Ни одно требование не закрывается словами «аналогично уже проверенному». Необходимы точные implementation/test/artifact references и результат применимого исполнения.

### 1.3. Границы владельцев

| Владелец | Где выполнять оставшуюся работу |
|---|---|
| `openpine-contracts` | Общие схемы, версии сообщений и типы на межкомпонентных границах. |
| `pine2ast` | Каталог, parser, typing/qualifiers, admission, связывание библиотек и исходная идентичность объявлений. |
| `ast2python` | Проверка producer bundle, lowering/emission, bindings, source maps и передача semantic origin. |
| `pinelib` | Значения, арифметика, builtin-функции, state/history/references, rollback/checkpoint и runtime ABI. |
| `backtest_engine` | Ордера, исполнения, позиции, сделки, риск, комиссии, margin и broker checkpoint. |
| `marketdata-provider` | Данные, revisions/finality, инструменты, calendars, offline admission и выборки. |
| `optimizer` | Поиск параметров, контракт runner, независимость trials, ranking и воспроизводимость поиска. |
| `openpine` | Effective configuration, orchestration, jobs/workers, публичные API/CLI, интеграция владельцев и verification. |
| `openpine-ui` внутри OpenPine | Редактор, настройки, данные, запуск, результаты, графики, диагностика и browser E2E. |

Если новый сквозной контракт требует изменений нескольких owners, сначала определить схему и ответственность, затем менять producer и consumers согласованно. Не устранять расхождение копированием бизнес-правила в gateway или test harness.

### 1.4. Обязательные ограничения реализации

- Не получать expected вызовом тестируемого OpenPine/PineLib или его обёртки. Независимые ручные расчёты и математические reference-реализации допустимы; реальные TradingView exports выделяются отдельно.
- Не сокращать обязательные scenarios, Python-матрицу, coverage и fault-проверки ради скорости или зелёного CI. Консолидация дублей допускается только с проверяемым соответствием прежних обязательств новым случаям.
- Не выключать sandbox и не подменять protected worker in-process исполнением. Process timeout сам по себе не является защитой от недоверенного кода.
- Не менять Pine version библиотеки на версию consumer; не использовать изменяемый глобальный semantic context между sessions/trials.
- Не смешивать `0`, `false`, пустую строку, Pine NA, JSON null и отсутствующее значение.
- Не переносить результаты и hashes между разными source candidates. Изменение исполняемого кода, tests, expected, plan, ABI или package inputs требует применимой новой проверки.
- Не вводить новую крупную подсистему только для удобства проверки существующей: расширять имеющиеся owners и публичные контракты.

<a id="integration"></a>
## 2. Согласованная сборка и завершение тестовой платформы

**Связанные OP:** 01, 03, 09, 11, 12, 24, 32–36.  
**Владельцы:** integrator и `openpine.verification`, совместно с владельцами библиотек.  
**Основания:** текущие refs/pins, PR #21/#22, workflow и execution owners [G01]–[G08], [G15]–[G18].

### INT-01. Свести полезные изменения в один candidate — И + К

1. Согласовать последовательность интеграции OpenPine PR #21 и stacked PR #22. Второй зависит от первого; проверять итоговое дерево, а не переносить двенадцать файлов поверх произвольной release-базы.
2. Подключить применимые изменения актуальных release-веток языковых библиотек. Текущий host pin ещё не охватывает весь свежий комплект каталогических и языковых публикаций.
3. Разобрать и интегрировать опубликованные узкие исправления: numeric-input completeness, reference closure, OCA policy identity, JSON checkpoint admission и offline ISO precision. Точные входные revisions перечислены в §13.
4. Для каждого изменения проверить ancestry и содержательный diff. Различать код, изменение ожидания по независимому контракту, новые тесты и metadata-only публикацию.
5. Устранить конфликты в catalog packs, target manifests, source pins, runtime/package locks, schemas и inventories. Пересчёт hash выполняется после согласования содержания, а не вместо него.
6. Сохранить штатные генераторы. Ручное исправление сгенерированного JSON без изменения его источника не принимается.
7. До удаления рабочих веток доказать сохранение уникальных изменений. Force push release и замена более свежего дерева архивной копией запрещены.

**Выход:** один manifest восьми компонентов с commit/tree/content identity, полный register решений по неприменённым изменениям и воспроизводимая сборка именно этого состава.

**Проверка:** новые language pins проходят producer→compiler→runtime→host; добавленные библиотечные тесты входят в общий inventory; никакая feature-ветка не считается интегрированной по наличию её имени в отчёте.

### INT-02. Завершить качество и исполняемость публикационного состава — Д + К

Исполнить реальные обязательные Ruff/format/type/compile/schema проверки на итоговом дереве. Исправить нарушения в коде и скриптах `scripts/rc6_stabilization/`, если они подтверждаются штатной командой. Нельзя закрывать такой пункт waiver, расширением ignore или объявлением файла «не относящимся к продукту», когда он участвует в проверке поставки.

Проверить совместимость фактических Python 3.11/3.12/3.13, build backends, explicit pytest plugins, Node/npm/browser и lockfiles. Библиотечные обязательства 3.12 сохраняются; обязательная host-приёмка выполняется на 3.11 и 3.13. Добавление отдельной дополнительной платформы не заменяет эту матрицу.

Preflight должен исполнять import/ABI handshake/minimal worker job и проверку ресурсов. Проверка только `which python`, наличия JSON или существования executable недостаточна.

**Выход:** успешные исходные проверки, environment identities и отсутствие подтверждённых открытых дефектов build/test harness на выбранном candidate.

### INT-03. Квалифицировать общую композицию owners — И + К

Довести действующие `test-ci`, `test-stabilization` и `test-current` до полного исполнения семи обязательных owners стабилизации:

| Owner gate | Что должно быть доказано |
|---|---|
| `branch-reconciliation` | Полный scope решений, существующие mappings, отсутствие необработанного полезного diff. |
| `foundation` | Архитектура, effective config, capability policy и зафиксированный независимый corpus на нужных Python. |
| `protected-workers` | Настоящие процессы, sandbox, lifecycle и cleanup, а не сохранённый флаг. |
| `coverage` | Полные component obligations, неизменённые пороги и объединённые первичные базы. |
| `frontend` | Тот же UI/backend candidate, реальные tests/build/browser и связанные outputs. |
| `packages` | Весь установленный стек, normal и rebuilt artifacts, origins/resources/semantics. |
| `test-performance` | Полные сопоставимые before/after измерения по INT-06. |

Проверять сначала общий campaign, затем принадлежность task fragments к этому campaign. Оптимизация повторного чтения evidence не должна разрешать чужой fragment, другую Python-среду, потерянный shard или неисполненную фазу.

Состояние `not_run` или `blocked` одного owner не закрывается успешными остальными. Сводка jobs не подменяет содержательную приёмку owners. Миниатюрная восьмикомпонентная fixture проверяет verifier, но не выполняет производственные обязательства.

**Негативная матрица:** missing owner; duplicate shard; foreign plan/run/source; испорченные JUnit/coverage; потерянный teardown; авария/timeout; невалидная связь normal/rebuilt package; чужой frontend dist; отсутствующий performance sample.

### INT-04. Закрыть переносимость evidence и полный жизненный цикл ресурсов — К + Д

Использовать текущую переносимую owner policy и frozen launch bindings. Не добавлять пути конкретного пользователя, локальный proxy или текущую рабочую директорию в нормативную семантику policy.

Требуется квалификация на двух независимых расположениях checkout/evidence и штатном CI runner:

1. Одинаковые inputs сохраняют content identity при переносе; locator не становится semantic identity.
2. Изменение bytes, executable bit, схемы, ожидаемого результата или использованного helper выявляется.
3. Outputs не записываются внутрь исходников и не создают цикл source→receipt→source.
4. Политика private retention удаляет только разрешённые временные данные успешного attempt. Failed logs, обязательные primaries, checkpoints пользователя и inputs сохраняются.
5. При превышении дискового/памятного бюджета процесс завершается контролируемо с evidence; очистка не касается соседнего запуска.
6. После cancel/crash не остаются дочерние процессы, открытые sockets, неполные публикации artifacts и блокировки, мешающие повтору.

Проверить не только один shard: несколько одновременно запущенных campaigns, полный process family и настоящие protected workers. Доказательства cleanup должны включать фактические surviving-process/resource наблюдения.

### INT-05. Завершить быстрый цикл разработки без неполной приёмки — К + Д

Квалифицировать существующие профили `smoke`, `affected`, `component`, `integration`, `stage-full`, `release-full` на реальных изменениях.

Для `affected` отдельно фиксируются test obligations и preparation dependencies. Проверить узкое локальное изменение, изменение consumer boundary, общей fixture, schema/catalog/ABI, runtime state, генератора и selection logic. Неизвестное изменение или отсутствующий selector вызывает отказ либо консервативное расширение до полного набора.

Для `smoke` использовать точные node IDs, включая параметризованные IDs. Набор должен проверять работоспособность owner, а не только наличие файлов или строк в YAML. Удалённый ID не игнорируется.

Сопоставить affected-план с full-run на наборе реальных patch scenarios. Найденный false negative блокирует доверие к selection до исправления. Полная приёмка версии не зависит от эвристики affected.

**Выход:** воспроизводимые быстрые профили, проверенная область безопасного сужения и опубликованные counts/hash/scope каждого плана.

### INT-06. Доказать ускорение всей тестовой системы — Д + К

Получить не менее пяти сопоставимых последовательных baseline и пяти after измерений полного согласованного обязательного scope. Один удачный run, отменённый baseline или измерение трёх библиотек не закрывает этот пункт.

Фиксировать отдельно collection/import, setup/call/teardown, повторные build/compile/catalog operations, worker startup, test execution, report/coverage aggregation, upload, queue и end-to-end wall time. В отчёте обязательны CPU-seconds/runner cost, peak RAM, дисковая нагрузка, число workers, cold/warm и исходные samples.

Оптимизировать подтверждённые узкие места: повторную неизменяемую preparation, вложенные полные pytest/build runs, одинаковые компиляции, повторный сбор больших каталогов, дисбаланс shards и ненужную сериализацию. Mutable session/heap/broker/request state не разделяется между тестами. Timeout/cancel/worker-startup проверки сохраняют настоящие процессы и временные границы.

| Контур | Цель при согласованном ресурсном профиле |
|---|---:|
| Warm smoke | ≤ 2 минут |
| Warm affected локального owner | ≤ 5 минут |
| Обычный PR без изменения общих семантических контрактов | ≤ 15 минут wall time без очереди |
| Полная функциональная кампания 3.11/3.13 | Цель ≤ 30 минут; обязательные 3.12 учитываются отдельно |
| Полный эквивалентный before/after scope | ≥ 2× по медианному wall time |

Указанные значения — критерии работы, не утверждение о достигнутой скорости. Недостигнутый бюджет остаётся открытым с критическим путём и причиной. Рост числа машин/CPU не выдаётся за устранение лишней работы; показывать цену ускорения. Продуктовые performance-проверки измеряются без coverage/profiler tracing; функциональное coverage сохраняется отдельно.

### INT-07. Исключить неоднозначность strict и diagnostic результатов — Д + К

Сохранить явное разделение диагностического отчёта и строгой языковой приёмки. В действующем workflow job с именем `strict-language` вызывает `--diagnostic-provisional`; для итоговой версии 5 это не строгая приёмка [G08].

Требуется:

1. Настоящий strict gate вызывает строгий путь без provisional-допуска и возвращает ненулевой exit при обязательном unresolved/failed/missing результате.
2. Diagnostic job может формировать неполный отчёт, но не удовлетворяет strict required check и не выставляет full language/release acceptance.
3. Ни имя job, ни поле `ok` верхнего orchestration-слоя не подменяет semantic verdict вложенного owner.
4. Неподтверждённые cases остаются в общем denominator и реестре остатка. Удаление записи не считается исправлением.
5. Проверки подмены охватывают diagnostic receipt под именем strict, другой registry/provenance, четвёртый необъявленный случай и runtime failure при неизменённой структуре отчёта.

**Выход:** однозначные CLI/check contracts; финальный release gate зависит от строгого содержательного результата.

### INT-08. Подготовить единую вычисляемую приёмку полной версии — Д + К

Расширить существующий acceptance owner результатами всех продуктовых областей. Не создавать отдельный «финальный» engine, обходящий `stage_gate` и текущие readers.

Stabilization verdict не принимает язык, данные, resume, broker или UX автоматически. Для 5.0 нужен общий current contract с обязательными областями, exact candidate, identities, проверенными первичными receipts и содержательными незакрытыми требованиями.

Обеспечить положительный полный путь принятия и отрицательный путь для каждого missing/stale/failed owner. Проверка, которая всегда возвращает `False`, также не является завершённой приёмочной системой.

**Выход:** одна вычисляемая current-проекция для CLI, progress/remainder, API, документации и release manifest. Источник истины — проверенные входы и исполнения, а не ручное переключение статусов.

<a id="language"></a>
## 3. Полная языковая область Pine v1–v6

**Связанные OP:** 02, 13, 14, 16–19, 31, 32.  
**Владельцы:** Pine2AST, AST2Python, PineLib; OpenPine отвечает за публичный и процессный путь.  
**Основания:** catalog/publication/remaining contracts, import version matrix, текущие библиотечные изменения [G09]–[G14], [G19]–[G22].

### LANG-01. Закрыть полноту version-exact contract, а не только inventory — Д + К

Для каждой обязательной сущности и формы вызова v1–v6 завершить прослеживаемый контракт:

| Измерение | Что должно быть определено |
|---|---|
| Идентичность | symbol, namespace, kind, canonical spelling/alias и overload ID. |
| Применимость | Версия появления/изменения/удаления, availability, deprecation, источник утверждения. |
| Вызов | Function/method/namespace form, receiver, positional/named, variadic и generic rules. |
| Параметры | Порядок, имена, types, qualifier ceilings, required/default и допустимые значения. |
| Результат | Тип, qualifier, tuple/collection/nominal shape, NA/error policy. |
| Исполнение | Runtime/delegated owner, exact target binding, допустимый type/value domain. |
| Основание | Версионно применимое внешнее утверждение или независимый расчёт именно проверяемого свойства. |
| Проверка | Positive/negative/version cases и применимые execution paths. |

Сопоставить требуемый внешний набор с producer и target в обе стороны. Отсутствие записи в установленном pack не доказывает `UNAVAILABLE` в языке. Неизвестная сигнатура не закрывается полем `any` или отсутствующим параметром.

Устранить обязательные gaps по qualifiers/defaults/returns/overloads и authority. Полнота имён, целостность pack и численная совместимость принимаются раздельно. `BOUND`, `RUNTIME_DIRECT` и `HOST_DELEGATED` не являются доказательством всей семантики.

**Выход:** полный required denominator, machine-readable mappings, проверяемые основания и отсутствие обязательного неопределённого contract dimension в принятой языковой области.

### LANG-02. Интегрировать и квалифицировать numeric inputs — И + Д + К

Включить guard numeric-input completeness из опубликованной ветки Pine2AST, сохранив независимость guard от генератора. Затем закрыть остающиеся внешние version-applicable contracts и всю цепочку input→folding→runtime→UI.

Обязательные случаи: `input.int/float`, generic input, `input.source`, bool/string/enum inputs; default `0`, `false`, пустая строка и typed NA; required vs omitted; bounds, step, options и их несовместимые сочетания; qualifier ceilings; active/display и прочие параметры только в применимых версиях; named/positional errors; duplicate/unknown arguments; большие exact integers без float round-trip.

Проверить dependency-safe default expressions и одинаковую семантику folding/runtime. Invalid value отклоняется до process spawn; после admission вложенный input descriptor нельзя изменить через исходный пользовательский объект.

Численные локальные invariants не подтверждают самостоятельно внешнюю версионную применимость. **Выход:** конкретные независимо подтверждённые input contracts и успешные обычные API/CLI/UI overrides на общем candidate.

### LANG-03. Довести binary-search family и `sort_field` до исполнения — И + Д + К

Не переоткрывать с нуля уже опубликованное решение о разделении v4/v5+; включить его применимые sources, отрицательные version tests и runtime expected в согласованный host-комплект. Дальнейшая работа — полная квалификация и закрытие недостающих форм, а не формальное восстановление удалённых строк.

Для scalar-array поиска проверить empty/singleton, exact hit, duplicates, before-first/after-last/between-neighbors, int/float, допустимые NA/error cases, sorting precondition и точные возвращаемые индексы. Сохранять unavailable identities прослеживаемыми, не допускать их как исполняемые overloads.

Для объявленного v6 UDT-array `sort_field` завершить public syntax → typing → overload/binding → compiler emission → runtime comparison. Зафиксировать по применимому основанию выбор поля по индексу/имени, default, допустимые типы поля и аргумента, ошибки неправильного поля, неоднородных данных и недопустимого qualifier. Metadata/default correction и публикационные hashes не принимают этот runtime contract.

**Проверка:** omitted/explicit default, named/positional form, field aliases/nominal identity, граничные индексы, duplicates, отрицательные versions/types, direct и compiled paths, JSON checkpoint/restore для сохраняемой ссылочной структуры. Expected не выводится из текущего поиска.

### LANG-04. Завершить применимость обновлённой языковой поверхности — Д + К

Для новых синтаксических форм, параметров и builtin signatures, входящих в зафиксированный scope 5.0, проверить generator/parser/typing/lowering/runtime/publication согласованно. Область фиксируется применимыми source snapshots, а не обещанием поддерживать все будущие изменения справочника.

Проверить, что metadata не объявляет callable форму поддержанной раньше её executable binding. Неприменимые ранние версии должны получать конкретную version diagnostic. Новые параметры не переносятся автоматически назад во все packs.

Для спорного статуса конструкции, в том числе `once`, различать подтверждённое языковое правило и явно согласованное расширение продукта. Не определять её статус только по имени файла или комментарию. Обязательная проектная функциональность при этом не исключается из scope.

### LANG-05. Принять полный контракт библиотечных imports — И + Д + К

Использовать существующие version matrix, linker, exact store и source-bound policies. Не строить второй resolver и не заменять разрешённые imports stateless-подмножеством.

Оставшаяся квалификация должна закрыть:

1. Весь применимый consumer×library version decision table; положительные allowed пары и отрицательные forbidden пары. Не менять направление допустимости из-за удобства реализации.
2. Source-origin semantics после linking, folding, lowering, emission и runtime. Version, declaration/overload и nominal identities входят в artifact/cache/checkpoint compatibility.
3. Все заявленные exports: functions, constants, methods, UDT, enum и допустимые reference/collection types; public/private visibility и transitive graph.
4. Nested/diamond graph, одинаковые имена разных modules/revisions, aliases, missing revision, cycle и checksum mismatch. Никакого latest/sibling fallback.
5. Named/default arguments, return qualifiers, scalar/tuple/reference crossing, default expressions и запрет private-type leakage.
6. Обычный compile API/CLI/gateway и UI-путь выбора точной библиотеки; ошибки показывают исходный library file/revision/line, не только generated Python.

**Выход:** все допустимые public export/context combinations квалифицированы на поставляемом стеке. Наличие одной allowed mixed pair или utility доказательства инвариантности не принимается за полноту imports.

### LANG-06. Закрыть source-origin поведение и изоляцию контекста — К + Д

Проверить существующие policies в реально импортированной библиотеке, вложенной библиотеке и root script:

- const/non-const int/float division, `%`, precision и promotion;
- bool/NA/coercion/first history и typed return;
- lazy/eager logical evaluation с побочными stateful effects;
- loop bound evaluation, break/continue и общий budget;
- выбор builtin/default/qualifier по origin библиотеки;
- UDT/enum/method declaration context и default initialization.

Два sessions/trials с разными версиями должны чередоваться без протекания policy. После exception/abort/cancel/restore semantic context вызывающего восстанавливается. Изменение module version или dependencies обязано инвалидировать несовместимые artifacts/checkpoints.

Expected для реально различающихся областей задаётся независимо. Равенство imported/native или v5/v6 используется как дополнительная проверка, не как единственное основание semantics.

### LANG-07. Закрыть полноту stateful language matrix — К + Д

Использовать реализованные UDF, history, loops, references, UDT/enum и methods. Требуется единая version×type×callsite×lifecycle матрица, а не повторное создание этих механизмов.

| Область | Остающееся обязательство квалификации |
|---|---|
| UDF | Два written callsites, повтор одного в loop, skipped calls, nested calls, captures, var/varip, defaults, returned history. |
| Bool/NA/history | Первый бар, отсутствующая/локальная история, typed NA, conditional execution, возвращаемые bool/NA, отсутствие Python truthiness substitution. |
| Arrays/maps/matrices | Aliases и независимые copies, nested/shared references, mutation/history, допустимые generic combinations и invalid types. |
| UDT/enum | Nominal identity разных exact modules, fields/defaults, persistence по полям, containers, method/argument/return/serialization. |
| Methods/overloads | Receiver+arguments+qualifiers+origin resolution, успешные перегрузки и ambiguity/private/recursion errors. |
| Loops as values | Scalar/tuple/reference/nominal results, empty/last-completed/break/continue/nesting, соседние blocks и общий callback budget. |
| `once` | Первое выполнение на unconfirmed tick, rollback/replay/commit, новый runtime после JSON restore, UDF/loop/import и применимые fill callbacks. |

Каждое обязательное взаимодействие должно быть покрыто конкретным случаем. Pairwise sampling не заменяет сочетания, прямо требуемые state/reference/version semantics.

### LANG-08. Интегрировать reference-closure repairs и проверить весь state graph — И + К

Свести heap-to-heap и external-storage closure исправления PineLib. Проверять не только вложенный объект, но и committed roots в series/history, slots, nested slot containers, UDF state и imported callsites.

Committed state не должен ссылаться на provisional allocation, исчезающую при rollback. Checksum-valid, заново запечатанный, но семантически некорректный checkpoint отвергается до изменения целевого runtime.

Квалифицировать на v5/v6 и применимых более ранних types: shared/nested aliases, per-field varip, два written calls, abort/replay, full/compact representation, новый runtime, несовместимая identity. После отказа все прежние owners и значения целевого runtime сохраняются.

**Выход:** direct runtime, compiled и host checkpoint paths подтверждают один reference graph contract. Только unit-проверка heap недостаточна для всей языковой области.

### LANG-09. Закрыть полный independent builtin oracle — Д + К

Для каждой обязательной versioned callable формы завершить contract-level coverage: independent expected, exact assignment, type/qualifier domain, edges/NA/warmup/errors/state и все применимые пути.

Различать `manual_fixture`, независимый математический derivation, `tradingview_export` и `missing`. Fixture обязан связывать Pine source/version, inputs/settings/data, overload/form, expected type/value/error/state, tolerances и provenance. Нельзя назначать один успешный scalar case всем overloads группы.

Обязательная матрица семейств:

| Семейство | Что закрыть кроме обычного positive value |
|---|---|
| Operators/conversions | Signs, exact integers, bool separation, division by zero, NA/null/missing, rounding и qualifiers. |
| Stateless numeric | Domain edges, small/large values, invalid transport, defaults/variadics и return type. |
| Rolling/statistical | Min/invalid/dynamic length, startup, NA внутри окна, eviction, flat/impulse/alternating inputs. |
| Recursive TA | Seed, первый валидный выход, update order, NA continuation, skipped calls и restored recurrence. |
| Event/history | Missing occurrence, первый бар, occurrence index, UDF/branch/loop contexts. |
| Arrays/maps/matrices | Empty/singleton, index/type/shape, mutation, aliasing/copy, iteration и serialization. |
| Strings/colors | Empty/bounds/occurrence/default/named forms, enum title, точные типы, color components/NA. |
| Delegated surface | Реальный host owner, language/ABI boundary и применимый продуктовый oracle; не фиктивный local numeric PASS. |

Отчёт должен обнаруживать orphan assignments, duplicated cases, чужую version/variant, missing path и первый divergence по bar/tick/callsite/source.

### LANG-10. Завершить неопределённые numeric contracts — Д + К

Отдельно закрыть оставшиеся независимые основания и поведение:

- RSI/MACD ранних Pine-версий: применимость, seed, промежуточные smoothing states, первый валидный output, NA/gaps и defaults. Современное описание не переносится назад без основания.
- TSI: обе цепочки сглаживания, startup/flat/zero denominator, первое изменение после flat, NA в разных фазах и продолжение после restore.
- `str.tonumber` для зарегистрированных спорных форм, включая `"1_000"`, `"1e2"` и `" 1 "`: установить version-applicable grammar и expected до изменения runtime или fixtures.
- Другие обязательные unresolved cases, обнаруженные полным LANG-09 denominator, не исключаются только потому, что отсутствуют в этом перечне.

**Выход:** независимые фиксированные expected и реальные applicable replay results. Generic unresolved registry остаётся диагностикой, а не способом принять неизвестную семантику.

### LANG-11. Исполнить полный corpus по всем путям — К + Д

Сохранить действующие группы и расширить их только по рассмотренным новым obligations:

| Группа | Variants | Обязательные paths |
|---|---|---|
| `builtin-array-concat` | full, compact | Все пять |
| `builtin-array-operations` | full, compact | Все пять |
| `builtin-event-history` | full, compact | Все пять |
| `builtin-map-operations` | full, compact | Все пять |
| `builtin-matrix-operations` | full, compact | Все пять |
| `builtin-numeric-closure` | full, compact | Все пять |
| `builtin-recursive-ta` | full, compact | Все пять |
| `builtin` | legacy format | Все пять |
| `builtin-rolling_statistics` | legacy format | Все пять |
| `builtin-scalar` | legacy format | Все пять |
| `builtin-string-operations` | full, compact | Все пять |
| `builtin-transcendental` | legacy format | Все пять |

Пути: `abi`, `compiled_historical`, `compiled_realtime`, `compiled_rollback`, `compiled_checkpoint`. `legacy format` — имя формата evidence, не разрешение legacy runtime fallback.

Для этого базового плана требуется 100 group×variant×path обязательств на каждую обязательную Python-ветку; это не число pytest cases и не доказательство достаточности всего oracle. Новые contracts расширяют план с явным review.

`compiled_checkpoint` должен сериализовать checkpoint и восстановить новый runtime. Realtime/rollback должны содержать настоящие unconfirmed updates. Переименование direct call в compiled path не принимается.

### LANG-12. Завершить согласованную языковую публикацию — И + Д + К

Согласовать producer catalog, compiler target и runtime ABI/publication/resource identities с host, библиотечными locks и установленными пакетами. Удалить противоречивые current-ссылки и claims, но не менять semantic inputs под желаемый результат.

Язык принимается только при одновременном закрытии:

```text
versioned_catalog
stateful_language_matrix
imports
independent_builtin_expected
```

Дополнительно: единый candidate, 3.11/3.13, обязательные library 3.12 checks, protected workers, неизменённые foundation obligations, frontend boundary и нормальный установленный package path. Отдельный принятый stabilization scope не удовлетворяет LANG-12.

**Выход раздела:** полный language verdict без обязательных `UNVERIFIED`, missing assignments и непройденных применимых paths; структурированные diagnostics и отсутствие сокращения scope.

<a id="data"></a>
## 4. Данные, инструменты и request

**Связанные OP:** 04, 08, 09, 21, 22; границы 02, 10, 26.  
**Владельцы:** compiler requirements — Pine2AST/AST2Python; snapshots/market metadata — Provider; orchestration — OpenPine; expression execution — PineLib.  
**Основания:** `request_requirements.py` ограничен ранней проверкой статических контекстов; он не является автоматическим loader. Offline precision repair опубликован отдельно [G23], [G24].

### DATA-01. Довести автоматическую подготовку request datasets — Д + К

Из существующего discovery/admission получить полный public pipeline:

```text
Pine / exact library graph
→ compiler request requirements
→ resolved instrument/timeframe/context graph
→ bounded data preparation
→ immutable admitted snapshots
→ runtime execution
```

Нельзя требовать, чтобы пользователь вручную угадывал все datasets, которые нужны скрипту. Статически разрешимые symbol/timeframe/settings извлекаются до запуска. Динамические requirements обрабатываются через ограниченный и воспроизводимый протокол подготовки/дозапроса; каждый фактически использованный dataset попадает в run identity.

Неразрешённый requirement не подменяется chart symbol/timeframe по умолчанию. Для отсутствующего источника выдаётся конкретная diagnostic: исходный request/callsite, instrument/timeframe, причина и разрешённое действие пользователя.

**Проверки:** несколько security contexts, одинаковый dataset у нескольких callsites, nested requests, зависимость от inputs, разрешённая динамика, duplicate/cycle/depth/size limits, cancellation во время подготовки, повтор без сети по закреплённым snapshots.

### DATA-02. Принять полный expression context для request — Д + К

Завершить применимые `request.security` и `request.security_lower_tf` contexts: nested calls, UDF/method/imported library, captured values, scalar/tuple/array/UDT results там, где они разрешены языковым контрактом.

Выражение исполняется в контексте запрошенного инструмента и периода. Пустые symbol/timeframe наследуют именно разрешённый enclosing context, не случайно root chart. Time/session metadata, NA/warmup и независимое callsite state проверяются внутри запроса.

Закрыть допустимую corporate/fundamental/currency/seed и другую заявленную `request.*` поверхность через настоящего Provider/host owner. Список форм берётся из принятого каталога. Для данных, недоступных провайдеру, нельзя возвращать правдоподобную пустую серию вместо определённого отказа.

**Проверки:** request внутри импортированной UDF, nested HTF→LTF, изменение input context, invalid timeframe/ignore flags, отсутствие серии, неверный тип выражения, runtime error с original source location.

### DATA-03. Завершить HTF/LTF alignment, sessions и calendar correctness — Д + К

Принять полный контракт lookahead/gaps, time/time_close, bar ownership и finality. Отдельно проверить fixed-duration и calendar periods; trading session, timezone, DST, выходные, праздники, короткие торговые дни и overnight boundaries.

HTF-значение не должно попадать в прошлый chart bar раньше допустимого события. LTF-массивы должны содержать правильные intrabars в хронологическом порядке и заданной временной границе, без повторного использования свечи в соседних chart bars.

**Матрица:** совпадающие/несовпадающие сетки; пропущенные бары; пустой/неполный интервал; первые/последние бары; session transition; DST; calendar month; nested request; preliminary→final. Expected задаётся событиями/временем и независимо размеченным соответствием, а не output текущего alignment.

### DATA-04. Довести revisions/finality до всех consumers — Д + К

Согласовать состояния preliminary, final, corrected и revoked между Provider, cache, request snapshots, runtime, job/checkpoint и UI.

Каждый snapshot имеет неизменяемую identity и provenance. Исправленная свеча не перезаписывает незаметно input уже принятого run. Новый revision создаёт новый dataset/run identity либо явно определённый разрешённый процесс перерасчёта.

Определить поведение при corrections после commit, отзыве данных и восстановлении job на изменившемся источнике. Cache не выдаёт более старый revision как новый; два параллельных trials не получают различные revisions под одинаковым hash.

**Проверки:** out-of-order updates, повтор одного revision, conflicting duplicate, final→corrected, revoked interval, concurrent readers, cache reload и checkpoint incompatibility. UI должен различать окончательный результат и результат на предварительных данных.

### DATA-05. Закрыть multi-market instrument metadata — Д + К

Для заявленных crypto/stocks/futures/FX paths завершить instrument contract: точный symbol/exchange/market, asset class, currencies, mintick, pointvalue, lot/quantity step, timezone, trading session и calendar.

Все consumers используют одно admitted представление. Нельзя извлекать tick size из случайного price sample, навязывать криптовалютную 24/7 session другим рынкам или подставлять нулевой pointvalue вместо ошибки.

Связать metadata с compilation/runtime/broker/request identities. Проверить conversion account/quote currency, изменение спецификации инструмента и несовместимость checkpoint после влияющего изменения.

**Выход:** supported-market matrix с реальными источниками metadata, deterministic fixtures и отрицательными случаями отсутствующих/противоречивых свойств.

### DATA-06. Завершить строгий offline CSV/Parquet путь — И + Д + К

Интегрировать точное ISO timestamp admission без потери дробной части при разборе времени и timezone offset. Проверить общую canonical identity через CSV и Parquet, а не только один serializer.

Оставшиеся требования:

1. Явная timestamp unit/timezone policy; никаких guess-by-magnitude или silent truncation.
2. Точная обработка aliases столбцов, duplicate/conflicting columns, OHLC validity, отсутствующего/нулевого volume, NaN/Infinity и out-of-order rows.
3. Один логический dataset из разных допустимых представлений даёт согласованную identity по документированной canonicalization policy.
4. Range/max-bars filters не скрывают запрещённую corruption, если контракт требует валидности всего admitted dataset.
5. Row-group filtering и indexed intrabar access не читают весь файл для каждого маленького запроса; результаты равны полной контрольной выборке.
6. Import preview и public API показывают строку/поле ошибки, не заменяют ошибочный dataset пустым успехом.

**Проверки:** millisecond precision и sub-millisecond rejection по заявленному формату, необычные допустимые separators, offset precision, границы эпохи, chunk boundaries, повторный import, повреждённый Parquet metadata и отмена длинного import.

### DATA-07. Завершить честный synthetic-chart contract — Д + К

Для каждого synthetic chart из согласованной поверхности определить algorithm/version, inputs, session/reset rules, repaint/finality и границы соответствия. Вычисление и broker use должны использовать один зафиксированный тип данных.

Не выдавать приблизительные synthetic bars за точное внешнее соответствие. Не применять synthetic OHLC как реальные исполнимые цены без явного broker contract. Неподдерживаемый тип не должен незаметно превращаться в обычные candles.

**Выход:** поддерживаемые виды с independently fixed fixtures, unsupported-by-design решения только для действительно исключённой области и понятный user-facing статус. Отсутствующая обязательная реализация не закрывается этим статусом автоматически.

### DATA-08. Принять данные сквозным продуктовым путём — К

Выполнить UI/API→discovery→provider/cache→admitted snapshots→worker→result→replay и optimizer-trial reuse на одном candidate. Подготовленные данные должны повторяться без обращения к latest.

**Выход раздела:** requests не требуют ручной подстановки скрытых datasets; HTF/LTF/session/revision contracts проверены; offline и multi-market inputs воспроизводимы; новые данные не меняют уже принятый run незаметно.

<a id="lifecycle"></a>
## 5. Lifecycle, полное восстановление и потоковый результат

**Связанные OP:** 05, 06, 10, 11, 25, 33; границы 04, 07, 17, 30.  
**Владельцы:** PineLib state, broker snapshots, Provider snapshots, OpenPine jobs/runtime/worker protocol.  
**Основания:** generated-session checkpoint ограничен committed boundaries; worker protocol не объявляет resume. Отдельная JSON admission-правка не содержит полного production typed decoder [G25]–[G28].

### RUN-01. Завершить единый event lifecycle — Д + К

Согласовать historical bars, historical fill recalculation, realtime updates, rollback/abort, commit и closed-bar publication между runtime, broker и host.

Для каждого события определить точный порядок: обновление data context, допустимый rollback, вычисление Pine, формирование intents, применение broker events, callbacks/recalculation и публикация результата. Counter/sequence/callsite identities не сбрасываются на вложенных вызовах.

Проверить применимые `calc_on_order_fills` и `process_orders_on_close`, несколько fills на одном баре, повтор callback после восстановления и первый unconfirmed bar. Корректность не определяется только последней equity-точкой.

**Выход:** одна причинная event-модель, которой пользуются bulk и interactive paths, с независимыми ожиданиями output/state transitions.

### RUN-02. Реализовать production typed restore из внешнего checkpoint — Д + И + К

Расширить штатную модель checkpoint/serializer, а не добавлять восстановление dataclasses в каждом тесте. JSON-loaded broker/runtime/statistics state должен проходить schema/type/value validation и восстанавливать новый engine/session через публичный API.

Требования к decoder:

- Versioned schema с явно допустимыми types/fields и миграцией либо определённым отказом.
- Восстановление Position, Order, Fill, Trade, statistics/equity/events и остальных разрешённых структур по зарегистрированным типам, без выполнения произвольного кода.
- Reject non-finite чисел, duplicate JSON keys, неверных индексов, невозможных quantities/states, повреждённых reference graphs и недопустимого размера/глубины.
- Integer/bool/NA/null сохраняют различия; generic dictionaries не принимаются вместо обязательной типизированной структуры.
- Проверка всего входа до атомарной замены живого state. Ошибка позднего поля не оставляет engine частично восстановленным.

Интегрировать опубликованный JSON corruption guard как часть этого пути. Его negative tests не заменяют positive restore.

**Приёмочный сценарий:** public export→реальные JSON bytes→завершение процесса→новый engine→public restore→продолжение. Без вспомогательного test-only rehydration.

### RUN-03. Довести checkpoint до полного задания — Д + К

Определить один атомарный согласованный cut между владельцами:

| Часть | Что входит в восстановление |
|---|---|
| Pine | Series/history, slots, var/varip, UDF/callsite state, references, nominal registry и semantic identities. |
| Broker | Pending/active orders, позиции, fills, открытые/закрытые trades, reservations, risk/margin/fees и intent bookkeeping. |
| Data/request | Snapshot/revision identities, request context/cache, progress по источникам и согласованный event cursor. |
| Visual/alert/log | Семантическое состояние объектов и последовательности опубликованных эффектов. |
| Job/protocol | Job/run IDs, worker generation, callback/intent/output sequence, last acknowledged frame и recovery boundary. |
| Compatibility | Source/artifact/library graph, config/inputs, catalog/ABI/policies и instrument/timeframe identities. |

Не сохранять ссылки на Python-объекты старого процесса. Checkpoint должен быть самодостаточен в пределах разрешённого artifact/snapshot bundle и явно указывать внешние immutable dependencies.

Определить поддерживаемые точки сохранения. Для неподдерживаемого промежуточного состояния нужен конкретный отказ или безопасное достижение следующей точки, не молчаливый пропуск pending effects.

### RUN-04. Реализовать и квалифицировать worker-protocol resume — Д + К

Расширить существующие worker protocol, handshake/capabilities и supervisor только после реализации полного восстановительного пути.

Host должен уметь запустить новый protected worker, проверить его exact capabilities/identity, передать checkpoint и immutable inputs, восстановить согласованный cursor и продолжить без повторного исполнения уже подтверждённых эффектов.

Различать повтор transport delivery, retry job и новый run. Нужен защищённый от неоднозначности acknowledgment/sequence protocol; старый worker generation не может допубликовать output в новый job.

**Проверки:** kill до/после checkpoint publication, после intent до acknowledgement, после fill, на callback boundary, во время output upload; повреждённый checkpoint; другой worker/ABI/library/data revision. Отказ не должен терять последний валидный checkpoint.

### RUN-05. Закрыть отсутствие повторных и потерянных эффектов — Д + К

Для orders, fills, alerts, visuals и result frames определить stable identity эффекта и правило повторной доставки/применения. Дедупликация не должна удалять два законных разных события с одинаковым текстом или ценой.

Проверить непрерывное исполнение и interrupted→resume исполнение на одном event stream. Сопоставляются все события, ordering, indices, object identities, quantities, cash/equity и output digests, а не только summary.

Если внешний канал не предоставляет exactly-once доставку, явно реализовать доступную модель: устойчивый outbox/idempotency и документированное поведение повторов. Не обещать недоказанную exactly-once семантику внешней системы.

### RUN-06. Завершить result profiles и bounded streaming — Д + К

Квалифицировать `full`, `metrics-only` и `streaming` как явные контракты. Отсутствие plots/trades/logs допустимо только в профиле, который их не обещает.

В `full` передаются все заявленные equity/trade/plot/visual/alert/log outputs с исходными identities. В `metrics-only` исключённые данные не вычисляются/не хранятся без необходимости, но численные итоговые metrics равны full для одинакового расчёта.

Существующий bounded chunk transport дополнить реальным backpressure и управлением частичным результатом, где это необходимо. Разделить «результат сериализован порциями» и «пользователь получает прогресс/данные во время расчёта».

**Проверки:** медленный/отключённый consumer, большой payload, frame reorder/duplicate/missing/corrupt, превышение лимита, cancel при чтении/сериализации/экспорте, сохранение terminal error и невозможность принять truncated stream как завершённый результат.

### RUN-07. Довести fault и resource-isolation матрицу — Д + К

Исполнить controlled faults на стадиях data load, compile, worker startup, historical loop, realtime update, broker fill, request, checkpoint, result streaming и export.

Для каждой точки обязательны: наблюдаемая ошибка, определённый job state, сохранённая диагностика, bounded cleanup, освобождение process family/file descriptors/locks и успешный следующий законный запуск.

Проверить файловые/network/process ограничения реального sandbox, доступ только к admitted inputs и отсутствие влияния одного trial/job на другой. Cgroup/pidfd/process-containment механизмы использовать по действующей архитектуре; не заменять тест настоящей изоляции mock-ответом.

### RUN-08. Устранить чрезмерную стоимость state identity — Д + К

Профилировать state hashing, checkpoint export и history serialization на длинных сериях. Устранить ненужное повторное полное хеширование/копирование растущего state, если оно даёт сверхлинейный рост.

Incremental identity разрешается только с доказанным равенством каноническому полному вычислению и корректностью после rollback, corrections, references mutation и restore. Нельзя исключать влияющий state из hash ради быстродействия.

**Выход раздела:** публичный full-job resume на новом protected worker, равенство непрерывному исполнению и полный fault/result-profile контракт.

<a id="broker"></a>
## 6. Стратегии и брокерская семантика

**Связанные OP:** 07, 20; границы 02, 05, 09, 10, 27, 30.  
**Владелец исполнения:** `backtest_engine`; OpenPine передаёт admitted config/intents и результаты без второй брокерской реализации.  
**Основания:** текущие strategy registry/host constraints сохраняют ограничения risk, indexed trade surface и fixed-stop+trailing; OCA repair опубликован отдельно [G29]–[G31].

### BROKER-01. Завершить заявленную strategy/risk/trade поверхность — Д + К

Довести compiler→host→broker mapping всех обязательных команд и accessor forms из принятого каталога: entry/order/exit/close/close_all/cancel/cancel_all, предусмотренные risk rules и indexed trade functions.

Не объявлять весь `strategy.*` поддержанным по наличию scalar snapshot `opentrades/closedtrades`. Индексированный accessor должен выбирать конкретную сделку и возвращать правильный тип/NA/error в соответствующей фазе.

Снять ограничения на допустимые risk declarations и аргументы там, где они являются ограничением реализации, а не языка. Risk semantics задаёт broker owner; ordinary branch evaluation не должно случайно выключать обязательную risk policy.

Завершить точные versioned signatures и допустимые positional tails. Named-only fallback не принимается вместо полной обязательной сигнатуры, если язык допускает позиционный вызов.

### BROKER-02. Завершить arbitration ценовых и trailing exits — Д + К

Реализовать и независимо проверить комбинации profit+limit, loss+stop, fixed stop+trailing, trailing activation price/points/offset, TP/trailing взаимодействие и version-specific приоритеты.

Поведение определяется по каждому opening fill и причинному price path. Общий средний entry price не заменяет контракт отдельной сделки. При нескольких одновременно достижимых уровнях решение воспроизводимо и подтверждено применимым правилом, а не порядком обхода dictionary.

**Матрица:** long/short, разные fill prices, gap, открытие за trigger, intra-bar activation, partial fill, замена exit, отмена, all-entry и explicit-entry scope, same-bar callbacks и restore.

### BROKER-03. Принять order lifecycle, reservations и multiple entries — Д + К

Закрыть полную комбинационную матрицу market/limit/stop/stop-limit, creation/activation/fill, pyramiding, same/different entry IDs, explicit qty/qty_percent/default sizing, partial exits, reservations, replacement/cancel и all-entry lifetime.

Сохранить zero/missing distinction: `qty=0` не превращается в default quantity. Незаконный размер/процент/type отклоняется определённо. Замена ордера не должна сохранять устаревший reservation или терять обязательные per-leg metadata.

Проверить вызовы до появления позиции, pending price entry, повторные entry ID, несколько exits на один entry и повторную активацию после cancel. Независимый event oracle должен включать intermediate states, не только финальную позицию.

### BROKER-04. Интегрировать OCA identity repair и закрыть group semantics — И + К

Группа определяется принятым контрактом имени и политики, не одним label. Интегрировать опубликованную правку `oca.py` и её рассмотренное изменение неверного тестового предположения.

Проверить cancel/reduce/none с одним именем, различными именами, explicit/implicit groups, partial quantities и несколькими opening fills. Исполнение одной политики не изменяет неучаствующие ордера другой политики.

Квалифицировать тот же event sequence через compiled Pine и после production JSON restore. Вспомогательная reconstruction в тесте не закрывает RUN-02.

### BROKER-05. Завершить FIFO/ANY и trade attribution — Д + К

Связать каждый fill/exit с правильными opening trades по выбранному правилу. Не ограничиваться равным aggregate PnL: должны совпадать закрытые quantities, времена/цены, commissions, entry/exit IDs и последовательность remaining open trades.

Проверить partial quantity, несколько entry одного ID, разные entry IDs, reversal, all-entry exit, overlapping reservations, stop/trailing, same-bar fill callbacks и resume. Формирование UI/optimizer отчёта должно использовать эти же trade identities.

### BROKER-06. Завершить broker environment и multi-market accounting — Д + К

Принять commissions, slippage, mintick/quantity rounding, pointvalue, margin/leverage/liquidation, account currency и необходимые conversions в полном заявленном scope.

Все параметры входят в effective config и run identity. Комиссия по ордеру/контракту/проценту применяется в определённый момент и правильно распределяется при partial fills. Повтор callback или replay не списывает её дважды.

**Матрица:** zero/nonzero fees, long/short, gap/slippage sign, rounding boundaries, insufficient capital/margin, changing position exposure, liquidation, futures pointvalue и currency conversion. Expected money/quantity/event values рассчитываются независимо.

### BROKER-07. Принять causal bar magnifier и intrabar execution — Д + К

Исполнение использует только события после создания/активации ордера. Нельзя воспользоваться high/low, произошедшим раньше этого момента, или intrabar другого chart bar.

Проверить chronological forward-only traversal, partial/empty intrabars, duplicate/revised intrabars, session boundaries, trigger gaps, несколько orders/exits и fill-recalculation. Подмена отсутствующих intrabars придуманным удачным price path не допускается.

### BROKER-08. Завершить независимую общую broker-приёмку — К

Выполнить матрицу BROKER-01–07 через direct engine, compiled Pine и поддерживаемые bulk/interactive paths. Для stateful случаев добавить public checkpoint/new-worker restore.

Сравнивать event-level expectations: создание/изменение/отмена/исполнение, orders, fills, trades, position, risk, cash/equity и fees. Численная близость одного итогового PnL не закрывает неверный event order.

**Выход раздела:** полный заявленный strategy/broker surface без «unsupported» вместо обязательных функций, versioned независимый oracle и согласованные публичные результаты.

<a id="performance"></a>
## 7. Производительность продукта и оптимизатор

**Связанные OP:** 11, 23–27; границы 04, 08, 10, 20, 28.  
**Владельцы:** runtime/storage — PineLib; compile/cache — AST2Python/OpenPine; broker/data — их owners; trials/ranking — Optimizer.  
**Основания:** действующие optimizer runner boundaries и сквозные требования версии [G32], [G33]. Этот раздел не подменяется ускорением pytest из INT-06.

### PERF-01. Получить полную сопоставимую benchmark matrix — К + Д

На точном согласованном стеке измерить 1k, 10k, 100k и 1M bars для репрезентативных workloads:

| Workload | Что измерять отдельно |
|---|---|
| Простой indicator | Compile, runtime hot loop, series/history storage и output export. |
| Stateful TA/UDF | Warmup, skipped calls, recurrence, state identity и память. |
| Reference-heavy script | Heap/alias/history, mutation, checkpoint и restore. |
| Multi-timeframe/request | Discovery/preparation, alignment, nested context и cache. |
| Strategy/broker | Pine time, order/fill processing, trade accounting и result generation. |
| Optimizer | Throughput, isolation overhead, cold/warm compile reuse и ranking/export. |
| UI-result workload | Размер/время доставки, browser parse/render и bounded memory. |

Сохранять hardware/OS/toolchain, real CPU quotas, memory/disk limits, inputs, semantic digests, cold/warm и исходные samples. Измерять latency без tracing; стоимость memory profiling показывать отдельно.

Не сравнивать разное число bars, урезанный output profile или разные semantic inputs как одну оптимизацию. Утверждение о превосходстве требует воспроизводимого baseline, не единичного удачного значения.

### PERF-02. Устранить подтверждённые runtime/storage bottlenecks — Д + К

По PERF-01 устранить избыточное copying, сериализацию, timestamp materialization, повторное вычисление одинаковых contracts и сверхлинейную обработку history/state.

History depth не сокращается без явного принятого контракта. Reference representation сохраняет aliasing/nominal identity; compact/full modes совпадают по обещанным outputs и восстановлению. Lazy/chunked processing не меняет порядок stateful updates.

**Проверки:** baseline/after semantic equality, memory growth по размеру данных, checkpoint/restore после оптимизации, cache-disabled path, long-run recurrence и последние/первые бары. Любая изменившаяся семантика рассматривается как отдельное изменение, не как ускорение.

### PERF-03. Завершить compile-cache и безопасную специализацию — Д + К

Принять существующий cache на полном identity:

```text
source bytes + Pine version + catalog/ABI + compiler options
+ exact transitive libraries + semantic origins/policies
+ affecting host capabilities/configuration
```

Проверить hit/miss/cold/warm, одновременное чтение/запись, прерванную запись, повреждённый artifact, eviction, устаревший ABI/catalog/library graph и смену relevant flags. Publication atomic; cache не является доверенным источником произвольного generated code.

Специализация допускается только по явно immutable inputs с доказанным равенством неспециализированному пути. Смена параметра или request context не использует чужую специализацию. Replay должен работать и без наличия локального cache.

### PERF-04. Принять throughput без скрытого переноса расходов — К

Показать отдельно compile time, bars/sec, peak memory, checkpoint/restore latency, streaming/export и optimizer throughput. Разделить подготовку и steady state, latency одного job и суммарный throughput параллельных jobs.

Для принятого resource profile исключить unbounded concurrency нескольких уровней: shards, optimizer workers, nested pools и build processes подчиняются общему бюджету. Очередь и degraded modes видимы, а не маскируются ростом timeout.

Точные дополнительные performance-SLO, которых нет в утверждённых требованиях, предварительно фиксируются как проектное решение; не назначать произвольный порог после получения результата.

### OPT-01. Принять общий runner contract для всех поддерживаемых путей — Д + К

Довести agreement между Optimizer, OpenPine adapter и backtest runner: parameters, effective config, source/artifact/library identity, data/snapshot revision, warmup, scoring range, result profile, error/status и output digests.

Проверить все фактически поддерживаемые runner-типы. Legacy contract spelling допускается только через явную migration boundary, а новые responses используют canonical contract. Нельзя принять incomplete response как completed trial.

**Выход:** одинаковый admitted request имеет прослеживаемую identity в trial, persisted report и replay; несовместимый runner/runtime/engine contract отклоняется до ranking.

### OPT-02. Завершить serial/parallel/seed/trial isolation — Д + К

На фиксированном candidate и data snapshots доказать равенство serial и parallel по контракту алгоритма: параметры trials, допустимая последовательность поиска, scores, selected winner и semantic outputs.

Mutable runtime/heap/broker/request/PRNG state не разделяется между trials. Неизменяемые data/build inputs можно переиспользовать по hash. Отменённый/упавший trial не загрязняет следующий.

Для адаптивных алгоритмов зафиксировать политику ordering завершений и влияние concurrency на поиск. Нельзя обещать одинаковый sequence, если он не входит в контракт; требуется воспроизводимый documented mode и replay.

**Проверки:** одинаковый seed, разные seeds, shuffled completion, timeout/crash/worker replacement, shared data mutation attempt, resume поиска и повтор на новом процессе.

### OPT-03. Закрыть scoring/ranking/error semantics — Д + К

Полноценно квалифицировать exclusion failed/cancelled/partial/nonfinite trials, сохранение законных нулевых metrics, constraints и tie-breaking. Missing metric не превращается в ноль; нулевой drawdown/число сделок обрабатываются по явному контракту.

Проверить money/percentage/ratio units, optimization direction, composite objectives и filter boundaries. Ranking использует только разрешённые scoring windows и не меняет source результаты trial.

**Выход:** независимые small-table ожидания ordering/eligibility и реальные runner cases дают одинаковый результат; повреждённый persisted trial обнаруживается.

### OPT-04. Воспроизводить победителя из отчёта и UI — Д + К

Обычный public replay получает exact settings, source/libraries, data revisions, period/warmup, semantic profile и artifact identity из выбранного trial. Не берёт текущие defaults или latest data.

Победитель из machine report, CLI и UI должен повторять обещанные metrics/trades/equity/digests. Несовместимость окружения или отсутствующий artifact показывает конкретный blocker и не заменяется похожим run.

Проверить export/import отчёта, новый процесс, очищенный compile cache и повтор после перезапуска приложения. Replay не должен требовать внутренних test-only объектов Optimizer.

### OPT-05. Принять train/validation/holdout и устойчивость — Д + К

Разделить warmup, train, validation и holdout в config, identity и отчётах. Holdout не участвует в выборе победителя, настройке thresholds или повторном подборе параметров без создания нового эксперимента.

Зафиксировать window boundaries, rolling/walk-forward policy, обработку warmup в каждом окне и воспроизводимость folds. Проверить отсутствие lookahead через request caches, data revisions и reused runtime state.

Результаты robustness/sensitivity/overfitting анализов должны ссылаться на реальные trials и методику. Отсутствующий анализ не подменяется декоративным confidence score.

**Выход раздела:** доказуемая скорость согласованного продукта и воспроизводимый optimizer с независимыми trials, честным ranking и обычным winner replay.

<a id="ux"></a>
## 8. Пользовательский интерфейс, visuals, alerts и диагностика

**Связанные OP:** 06, 25, 27–32; границы 02, 08, 10.  
**Владельцы:** OpenPine gateway/runtime/export и `openpine-ui`; семантические tapes остаются у PineLib/соответствующего owner.  
**Основания:** текущий frontend включает Vitest, Node checks и Playwright; требуется квалификация продукта, а не создание второго тестового UI [G34], [G35].

### UI-01. Завершить единый пользовательский путь — Д + К

Принять в настоящем browser:

```text
Editor/source + exact libraries
→ compile/diagnostics
→ inputs/effective settings
→ discovered data requirements
→ prepare/admit data
→ run/progress/cancel
→ result/chart/trades/logs/alerts
→ export/replay
```

Проверить ordinary indicator, strategy, permitted library import и multi-timeframe script. UI должен показывать применённые параметры, период, инструмент, data identity/revision и выбранный result profile, а не только форму до запуска.

Изменение source/input/data после compile/run не должно незаметно подменять identity уже выбранного результата. Создание стратегии не проходит без обязательных admitted inputs/artifact.

### UI-02. Закрыть все обязательные отрицательные пути — Д + К

Содержательная диагностическая матрица:

| Ситуация | Обязательное поведение |
|---|---|
| Syntax/type/version error | Исходный Pine file/line/column и объяснение, без сырого внутреннего traceback вместо сообщения. |
| Missing exact library | Revision/checksum/module и путь исправления; без latest substitution. |
| Missing MTF/dataset | Конкретные symbol/timeframe/context и доступное действие подготовки. |
| Unsupported capability | Точная capability и область ограничения, не ложный успешный empty result. |
| Invalid input | Поле, ожидаемый контракт и сохранение исходного введённого значения. |
| Worker failure/timeout | Terminal status, причина, сохранённые logs и возможность законного повторного запуска. |
| Cancel/partial result | Отличие отмены от ошибки/успеха, статус сохранённых outputs/checkpoint и возможность resume. |
| Incompatible replay/checkpoint | Конкретное несовпадение source/data/config/ABI, без скрытого пересчёта на новых inputs. |

Не требуется новая система ошибок поверх существующей: довести структурированные domain errors до всех публичных consumers.

### UI-03. Квалифицировать гонки загрузки и большие результаты — К + Д

Проверить 100k, 300k и 1M точек на реальных экранах и production build. Измерить load/parse/render/interaction time и browser memory на указанном reference profile.

Sampling/virtualization должны сохранять экстремумы, временную привязку и допустимую детализацию. Ограничение labels/top-N не меняет исходные экспортируемые данные. Offscreen divergence нельзя искусственно притягивать к границе viewport.

Гонки: A→B при задержанном A; повтор того же run; late error; unmount; reconnect; cancel; смена периода/инструмента; чужой run_id в payload. Поздний ответ не перезаписывает текущий результат, loading или error state.

При больших данных UI остаётся отменяемым и не блокирует пользователя неограниченной синхронной обработкой.

### UI-04. Завершить visuals как семантические объекты продукта — Д + К

Для заявленных plot/hline/fill/label/line/box/table и соответствующих visual forms обеспечить полный путь runtime tape→result transport/export→renderer.

Проверить stable object IDs, create/update/delete, координаты/time/index, NA, styles/colors и lifecycle. Realtime rollback должен откатывать неподтверждённые visual effects, а restore — сохранять committed identity без duplicates.

Численные значения и geometry в exports должны соответствовать runtime; renderer не рассчитывает заново Pine semantics. `metrics-only` не обещает несуществующие visuals, а `full` не теряет их молча.

### UI-05. Завершить alerts, order metadata и delivery semantics — Д + К

Разделить alert condition, вычисление message, order alerts, external delivery и persisted outbox. `disable_alert` влияет на alert, а не на факт исполнения ордера.

Message вычисляется в правильном event/context и сохраняется с соответствующими run/order/trade/source identities. Per-leg metadata не заменяется поздним текущим значением переменной.

Проверить realtime повтор, rollback, fill callbacks, cancel, transport retry и полный resume. Внешняя delivery failure не превращает корректный backtest в ложный успех доставки и не вызывает повтор сделки.

Для каждого канала явно показать supported/disabled/configuration-required state. Не публиковать секреты или credentials в logs/artifacts.

### UI-06. Довести единый conformance/first-divergence отчёт — Д + К

Сопоставлять одинаковые source/data/settings/version identities и отдельно классифицировать:

- language/runtime divergence;
- broker/lifecycle divergence;
- data/vendor/session/revision mismatch;
- неподтверждённое external expected;
- ошибка harness/transport/environment.

Показывать первый divergent bar/tick/phase/callsite, expected/observed и точные типы/NA, tolerance, original source и затронутое состояние. Средняя ошибка или последний совпавший output не скрывает ранний неверный warmup.

Связать CLI/API отчёт, parity UI и экспорт с одним проверенным machine result. Нельзя UI-словом «совпадает» повысить `manual_fixture` до `tradingview_verified`.

### UI-07. Принять frontend/backend/package совместимость — К + Д

Исполнить штатные commands из текущего package manifest: tests, coverage, production build, Node checks и Playwright E2E. OpenAPI/client schema получается из того же backend candidate; generated client drift вызывает отказ.

Доказать связь frontend execution root, frozen source inventory, command inputs, test reporter output и полного dist. Скопированный чужой `dist` не принимается по вручную совпавшему descriptor.

Проверить installed deployment path, API version/stack manifest, authentication/session handling в затронутых сценариях и отсутствие обращения браузера к случайному development backend.

**Выход раздела:** полный ordinary browser workflow и negative matrix на поставляемых artifacts; component/Vue unit tests не заменяют эти проверки.

<a id="delivery"></a>
## 9. Установка, совместимость и выпуск 5.0.0

**Связанные OP:** 01, 09, 12, 34–36; финальная приёмка всех остальных.  
**Владельцы:** integrator, package/distribution owners, OpenPine current/release readers.  
**Основания:** существующие package/frontend/native owners должны быть квалифицированы вместе; наличие scoped self-checks не удостоверяет полный продукт [G04], [G06]–[G08], [G15], [G17].

### REL-01. Квалифицировать normal и sdist-rebuilt packages всех восьми owners — И + Д + К

Собрать настоящие wheel и sdist штатными backends из exact candidate. Для каждого sdist выполнить отдельный build-from-sdist вне исходного checkout.

Для normal и rebuilt наборов отдельно выполнить установку полного восьмикомпонентного стека на Python 3.11/3.12/3.13 согласно текущему package scope. Не подменять rebuilt qualification проверкой normal wheel.

Проверить contents/resource closure: каталоги, target/ABI, schemas, publication metadata, runtime/stack locks, library/origin resources и другие файлы, которые реально читает installed path. Отсутствующий ресурс не должен находиться через соседний checkout.

Source/build/artifact identities вычисляются существующими owners по документированной политике. Допустимые различия build metadata не объявлять semantic mismatch; изменившиеся исполняемые bytes не маскировать общей версией пакета.

### REL-02. Принять чистый installed path — Д + К

Каждая квалификационная установка выполняется в отдельной чистой среде, из рабочего каталога вне source roots, без editable install, `PYTHONPATH` и shadowing production packages тестовым checkout.

Зафиксировать distributions/versions, interpreter binary/version, `__file__`/resource origins, artifact hashes, installed prefix и dependency closure. Проверить, что установлены все восемь соответствующих пакетов, а не смесь RC6 и старой distribution с тем же import name.

Обязательные public probes: API version/stack manifest; Pine compile/run; exact library import; runtime state/checkpoint; request/resource access по принятому scope; negative missing/tampered resource; foreign source shadowing; incompatible ABI/catalog.

Тесты, которым необходим исходный build root, получают явное проверенное соответствие roots, а не используют `module.__file__` установленного пакета как директорию проекта.

### REL-03. Завершить runtime/package/API/frontend version coherence — Д + К

Согласовать package versions, stack lock, API `/version`-контракт, catalog/ABI fingerprints, frontend build identity, migrations и distribution manifest.

Переход к `5.0.0` — отдельный финальный candidate, если меняет исполняемые package inputs. Не переносить PASS с rc6 wheel на изменённый final wheel только потому, что «поменялся номер».

Документировать, какие fingerprints обозначают source revision, package bytes, semantic ABI и данные. Несовпадение Git commit при идентичных tree bytes не скрывать и не объявлять автоматически семантической ошибкой; квалификация сохраняет фактический build provenance.

### REL-04. Завершить read-only doctor и диагностику установки — Д + К

Штатная команда проверки должна определённо диагностировать:

| Проверка | Ожидаемый результат |
|---|---|
| Полнота стека и версии | Все восемь owners соответствуют manifest. |
| Catalog/ABI/contracts | Совместимость producer/compiler/runtime/host; конкретное расхождение. |
| Resources и package data | Читаются из installed artifact и совпадают по identity. |
| Worker/sandbox | Реальный безопасный handshake/minimal probe и доступные ограничения. |
| Provider/data | Конфигурация, capabilities и доступность допустимых inputs без скрытой подмены. |
| Frontend/backend | Совпадающие API/schema/build contracts. |
| Storage/permissions/cache | Доступность путей, безопасные ownership/permissions и валидность необходимых cache entries. |
| Library/checkpoint/artifact compatibility | Понятный supported/migration-required/rejected статус. |

Doctor не изменяет пользовательские данные, не удаляет cache/checkpoints автоматически и не исправляет несовместимость незаметной переустановкой. Repair/migration — отдельное явно подтверждённое действие.

### REL-05. Завершить migrations и удалить только доказанно лишний compatibility-код — Д + К

Для checkpoint, generated artifacts, library locks, config, datasets и persisted reports определить совместимость с финальной версией. Для каждого формата — supported as-is, явная проверяемая migration либо структурированный отказ.

Migration сохраняет резервную копию/исходную identity, выполняется атомарно и не меняет semantic inputs без уведомления. Проверить повторный запуск, прерывание, corruption и rollback самой migration.

Удалять duplicate/legacy adapters только после поиска consumers, переноса необходимых сценариев и доказательства эквивалентности. Не оставлять скрытый fallback на другую библиотеку/runtime; но и не удалять действующий compatibility path только ради «чистого дерева».

### REL-06. Принять весь release единым содержательным gate — Д + К

Расширить существующие stage/current/release owners общим verdict полного продукта. Финальный required result должен проверять не только job conclusions, но и содержимое raw receipts каждой области §10.

Нельзя принять release по одному из следующих результатов: platform self-check; component suite; foundation; stabilization; diagnostic language report; наличие wheel; source hash; browser smoke; positive verifier fixture.

При полном валидном комплекте gate выдаёт Accepted для явно указанного final candidate. При missing/failed/unresolved обязательстве — неуспех, точные blockers и сохранённые первичные данные. Отрицательный результат не исправляется ручным изменением `status`.

### REL-07. Подготовить одну воспроизводимую поставку — И + К

Итоговый комплект должен содержать:

```text
README с проверенными командами установки и проверки
FINAL_REPORT с точным scope и фактическими результатами
DELIVERY_MANIFEST с identities и checksum descriptors
согласованные sources восьми компонентов и UI
реальные wheel/sdist и зависимости либо точный воспроизводимый dependency manifest
policy/plan/inventory/independent fixtures, необходимые проверке
raw evidence и единый current/release verdict
migration/doctor compatibility information
проверяемая история функциональных патчей, необходимая для происхождения поставки
```

Это логический состав, а не требование создать конкурирующее дерево поверх действующего layout. Не включать secrets, credential caches, чужие пользовательские datasets, venv/node_modules и случайные build outputs.

Не заявлять offline-ready, если сторонние зависимости не поставлены и offline installation не проверена. Если нужен network install по locked dependencies, это должно быть указано явно.

### REL-08. Проверить созданный архив и финальную публикацию — К

Проверка выполняется на реальном готовом архиве:

1. CRC, полный file manifest, отсутствие traversal/symlink escape/неожиданных executable members.
2. Распаковка в независимый каталог; сверка source/artifact/evidence hashes и присутствия всех owners.
3. Запуск поставляемого verifier и installed smoke без обращения к исходной рабочей директории.
4. Повторное чтение current/release verdict и доказательство связи с испытанными bytes.
5. Проверка README-команд, версии API/UI, package resources и manifest consistency.
6. SHA-256 окончательного архива; любое изменение архива требует новой проверки целостности.

При отдельно разрешённой Git/tag/release публикации выполнить remote readback. Публикуемые refs и artifacts должны соответствовать квалифицированному составу. Один merge или тег не принимает версию автоматически.

**Выход раздела:** устанавливаемая версия 5.0.0 с воспроизводимой проверкой, миграциями и единственным актуальным release verdict.

<a id="acceptance"></a>
## 10. Общая матрица проверок и правило приёмки

### 10.1. Единая трассировка оставшихся требований

Для каждого ID настоящего документа создать запись в существующем requirements/acceptance registry:

```text
requirement_id
source requirement / OP mapping
owner + affected consumers
work type: implementation / integration / qualification
applicability: versions, types, contexts, lifecycle, execution paths
implementation references
independent contract/expected references where required
positive / negative / regression test IDs
candidate / plan / inventory / package identities
raw execution receipt descriptors
status + concrete remaining reason
```

Запись без test/evidence linkage не закрывается. `not_applicable` требует основания по языку или утверждённым границам продукта. «Нет реализации» не является основанием неприменимости.

Список существующих tests разрешено использовать без их переименования. Новые IDs в этом ТЗ — идентификаторы требований, а не утверждение о существовании одноимённых CLI/tests.

### 10.2. Обязательный финальный набор

| Gate | Полная обязательная область | Что не заменяет результат |
|---|---|---|
| Candidate integrity | Восемь owners, UI, dependencies, generated inputs и package identity. | Только Git SHA host. |
| Static/build quality | Обязательные lint/format/type/schema/import/build checks затронутых и релизных owners. | Один compileall. |
| Functional matrix | Полные mandatory suites и интеграция на 3.11/3.13; обязательные library 3.12. | Collect-only или targeted pack. |
| Language | Четыре основных критерия и весь применимый versioned surface. | `BOUND`, несколько примеров или diagnostic mode. |
| Independent oracle | Required cases/assignments/paths с независимыми expected. | Direct==compiled self-comparison. |
| Data/request | Discovery, preparation, HTF/LTF, revisions, offline, markets и public replay. | Ручная подстановка пары datasets. |
| Lifecycle/resume | Новый protected worker, полный checkpoint и причинное продолжение. | Checkpoint одного RuntimeSession. |
| Broker | Команды/risk/indexed trades, price/OCA/FIFO/margin и independent event oracle. | Итоговый PnL. |
| Optimizer | Runner, isolation/seed, ranking, winner replay и независимый holdout. | Один dummy runner. |
| Frontend | Production build, unit/contract/browser E2E, реальные negative paths и большие результаты. | Наличие Playwright в package.json. |
| Packages | Все normal/rebuilt stacks, clean installed paths, origins/resources/semantics. | Wheel build без запуска. |
| Performance | Эквивалентный полный test before/after и отдельные product workloads. | Ускорение малого subset. |
| Fault/sandbox | Все затронутые фазы, process family cleanup и действующие ограничения. | Mock subprocess либо timeout только родителя. |
| Delivery | Doctor/migrations, actual archive round-trip и единый verdict. | Успешный ZIP CRC. |

### 10.3. Inventory и execution receipts

До выполнения фиксируются required inventory, selection, assignment каждому shard, Python/environment identity и expected/corpus hashes. После выполнения агрегатор подтверждает:

```text
union(executed required obligations) == frozen required obligations
missing == 0
unexpected == 0
unexplained duplicates == 0
all required setup/call/teardown phases are successful
all candidate/plan/environment/corpus/artifact identities match
```

Считать obligation по component/test-or-case/Python/path/variant/profile, а не по числу строк stdout. Один testcase с тремя reports не является тремя тестами. Source-mode и installed-mode результаты не смешиваются без соответствующего scope.

Отрицательный тест считается успешным только если исполнил требуемый контракт отказа. Тест, ожидающий отказ обязательной неготовой функции, не принимает саму функцию.

### 10.4. Политика изменения тестов

Запрещено получать успех удалением failing cases, широким deselection, `skip/xfail`, ослаблением assertions/coverage/tolerances или подгонкой expected под runtime.

Если ошибочно исходное предположение теста, требуется независимый контракт, сохранённый контрпример и явное изменение assumptions. При консолидации: old obligation→new cases, все значимые assertions/negative/version/lifecycle interactions, reviewed inventory delta и исполнение нового набора.

Широкий растущий test count не доказывает сохранение потерянного сценария. Файловый hash теста также не заменяет проверку его meaningful assertions.

### 10.5. Positive/negative тесты самой приёмки

Минимальная матрица нарушений, каждое из которых должно блокировать соответствующий gate:

| Подмена или сбой | Требуемый исход |
|---|---|
| Удалён один обязательный suite/path/interpreter | Неуспех, missing obligation в отчёте. |
| Подменён source/catalog/ABI/library/data identity | Неуспех до исполнения либо отказ reuse/acceptance. |
| Скопирован успешный receipt другого run/candidate | Неуспех по identities и primaries. |
| Внешне валидный, но пустой/неполный JUnit | Неуспех по точному inventory/phases. |
| Crash, timeout, failed teardown, interrupted upload | Неуспех; данные сбоя сохранены. |
| Diagnostic language receipt под видом strict | Неуспех полного language/release gate. |
| Ресурс отсутствует в wheel, но существует в checkout | Installed gate выявляет shadowing/отсутствие. |
| Rebuilt wheel испорчен при успешном normal wheel | Неуспех rebuilt/package owner. |
| Frontend tests/build выполнены на другом UI | Неуспех по execution-root и artifact provenance. |
| Runtime checkpoint заново захеширован после некорректной mutation | Semantic validation отвергает до замены state. |
| Повторяется один performance sample или изменён resource budget | Неуспех performance qualification. |
| Приёмочная сводка отредактирована без изменения primaries | Replay не совпадает со сводкой; неуспех. |
| Неполный scope назван `full_release_accepted=true` | Неуспех consistency/required-domain checks. |

Должен существовать и реальный полный положительный путь. Проверки verifier на миниатюрной fixture дополняются квалификацией реального продукта; они не складываются с неподтверждёнными production summary в Accepted.

### 10.6. Независимость численных оснований

Для integers, bool, indices, strings, nominal IDs и NA/missing структуры использовать точное сравнение. Для float tolerance назначается по конкретному контракту/формуле и размеру вычисления, не единым произвольным числом.

Reference calculation не импортирует тестируемый runtime/compiler, не читает current observed и не выбирает seed по результату сравнения. Source/derivation должен быть применим именно к проверяемой версии и форме.

Реальный внешний TradingView export получает `tradingview_verified` только для совпадающих source/data/settings/version и проверенного результата. Manual fixture не нуждается в ложном TV-флаге для собственной приёмки.

### 10.7. Обращение с ошибками и повторными запусками

Различать product regression, contract gap, harness defect, environment failure и отсутствие независимого expected. Ненулевой exit не диагностируется автоматически как ошибка Pine.

Первый failure сохраняется вместе с причиной повторного запуска. После изменения code/tests/expected/plan формируется новый candidate. Успех после retry без объяснения flaky behavior не закрывает дефект.

Параллельные jobs разрешены, когда принадлежат одной кампании точного candidate. Нельзя собирать финальную приёмку из несвязанных PASS нескольких revisions.

### 10.8. Вычисляемый Definition of Done OpenPine 5.0

```text
full_release_accepted =
    coordinated_eight_component_candidate
    AND current_source_and_package_identity_verified
    AND mandatory_functional_and_quality_checks_pass
    AND complete_language_contract_accepted
    AND independent_builtin_oracle_accepted
    AND data_and_request_contract_accepted
    AND lifecycle_and_full_job_resume_accepted
    AND complete_strategy_broker_contract_accepted
    AND optimizer_and_winner_replay_accepted
    AND frontend_browser_result_contract_accepted
    AND required_fault_and_sandbox_checks_pass
    AND all_normal_and_rebuilt_installed_package_checks_pass
    AND required_test_and_product_performance_qualification_pass
    AND doctor_migrations_and_delivery_roundtrip_pass
    AND no_open_mandatory_requirement
```

Схема — логическое требование, не готовый replacement API. Её реализует существующий acceptance owner с проверкой raw evidence.

`blocked_by_environment` и `blocked_by_external_information` объясняют незавершённость, но не являются альтернативным зелёным завершением обязательного scope. Исключение области продукта требует отдельного явного решения владельца требований; его нельзя оформить исполнителем для удобства выпуска.

<a id="execution"></a>
## 11. Порядок выполнения и передача результата

### 11.1. Очерёдность

| Порядок | Работа | Условие перехода |
|---|---|---|
| 1 | INT-01/02: согласованный candidate, узкие интеграционные repairs и действующая среда. | Источники/контракты совместимы; базовые проверки исполнимы. |
| 2 | INT-03–08: общая test/owner композиция и qualification; LANG integration и DATA discovery могут выполняться по независимым owners. | Нет скрытой неполноты evidence; изменения language/data прослеживаемы. |
| 3 | LANG-01–12: полный язык и independent oracle; устранение выявленных regressions. | Содержательная языковая приёмка, не только metadata. |
| 4 | DATA-01–08, RUN-01–08 и BROKER-01–08 в согласованном dependency order. | Полные data/lifecycle/broker boundaries и resume contract. |
| 5 | PERF/OPT и UI: квалификация на стабильных product contracts. | Быстродействие, воспроизводимый winner и обычные browser paths. |
| 6 | REL-01–08 и общий §10 gate. | Один финальный 5.0.0 candidate, проверенные packages и фактическая поставка. |

Инфраструктурную интеграцию и подготовку package/browser owners не откладывать до последнего шага. Но не менять одновременно один semantic owner несколькими независимыми исполнителями. Общие schema changes согласовываются до реализации consumers.

### 11.2. Минимальный результат каждого рабочего пакета

Каждый пакет возвращает implementation/test changes либо доказанную квалификацию без лишнего изменения кода; requirement IDs; independent basis там, где необходим; exact source/plan/environment; executed results и raw artifacts; влияние на schema/catalog/ABI/inputs; конкретный незакрытый остаток.

Количество коммитов, строк или тестов не является процентом готовности. Отдельный successful scope может быть опубликован с точным названием, но не повышается автоматически до полного продукта.

### 11.3. Требования к итоговому отчёту

Итоговый отчёт содержит только актуальный результат поставляемого candidate:

- таблицу требований настоящего ТЗ и исходных OP с фактическими code/test/evidence references;
- exact revisions/tree hashes восьми компонентов, frontend и build/dependency identities;
- результаты каждого обязательного component/interpreter/path и owner gate;
- independent conformance и первые divergence при наличии;
- нормальные/rebuilt package и installed probes, doctor/migrations/round-trip;
- performance samples, ресурсы и сравнимый baseline;
- реальные failures/retries с причинами, не очищенную картину одних PASS;
- один current/release verdict и проверенные команды воспроизведения.

Нельзя писать «всё готово», когда остался обязательный unresolved, partial или not-run scope. Вместе с отчётом передаётся сам продуктовый комплект, не только список планов или отчётов.

<a id="traceability"></a>
## 12. Трассировка исходных OP-01–OP-36

Это соответствие **оставшихся обязательств**, а не таблица выполненного. Для сквозных OP требуется итоговая проверка на версии 5; это не задание повторно написать их существующую реализацию.

| OP | Оставшееся обязательство версии 5 | Требования этого документа |
|---|---|---|
| OP-01 | Воспроизводимая согласованная поставка и installed execution. | INT-01, REL-01–03, REL-06–08 |
| OP-02 | Полная input семантика и единый публичный путь параметров. | LANG-02, UI-01/02, BROKER-03 |
| OP-03 | Финальная сквозная проверка effective config без второго преобразователя. | INT-03/08, RUN-03, OPT-01, REL-03 |
| OP-04 | Revisions/finality между data, cache, execution и replay. | DATA-04/08, RUN-03 |
| OP-05 | Общий event lifecycle и корректные callbacks. | RUN-01, LANG-07, BROKER-02/07 |
| OP-06 | Полнота обещанного bulk/result profile. | RUN-06, UI-04/06 |
| OP-07 | Полный strategy-host→broker contract и state projection. | BROKER-01–08, RUN-01/03 |
| OP-08 | Автоматические requirements и полный request context. | DATA-01–03/08 |
| OP-09 | Согласованные instrument/timeframe/language identities. | INT-01, DATA-05, LANG-12, REL-03 |
| OP-10 | Полное публичное восстановление задания на новом worker. | RUN-02–05, REL-05 |
| OP-11 | Стоимость state hashes/checkpoint без потери identity. | RUN-08, PERF-01/02 |
| OP-12 | Общая full-scope multi-interpreter приёмка. | INT-02/03/08, REL-06, §10 |
| OP-13 | Полная qualification `once`, включая unconfirmed/resume/fill paths. | LANG-07, RUN-01/05 |
| OP-14 | Version-exact каталог и исполняемые contracts. | LANG-01/03/04/12 |
| OP-15 | Сквозная достоверность capability graph и absence vs unsupported distinction. | INT-03/08, LANG-01, DATA-02, BROKER-01 |
| OP-16 | Полная bool/NA/history/version qualification. | LANG-06/07/09 |
| OP-17 | Stateful UDF/reference/callsite/lifecycle полнота. | LANG-05–08, RUN-01–05 |
| OP-18 | Required builtins и независимый полный oracle. | LANG-03/09–11 |
| OP-19 | Полная применимость зафиксированной обновлённой языковой поверхности. | LANG-01/03/04/12 |
| OP-20 | Полные broker/order/trade/risk/accounting semantics. | BROKER-01–08 |
| OP-21 | Точный deterministic offline data path и эффективные выборки. | DATA-06/08 |
| OP-22 | Multi-market/session/synthetic contracts. | DATA-03/05/07 |
| OP-23 | Runtime storage/hot-loop performance на полной матрице. | PERF-01/02/04 |
| OP-24 | Compile cache, invalidation и безопасная специализация. | PERF-03, LANG-05/06 |
| OP-25 | Streaming/backpressure/progress/cancel/partial/resume. | RUN-06/07, UI-01/02 |
| OP-26 | Runner contract, trial isolation и reproducible parallel execution. | OPT-01/02 |
| OP-27 | Честный ranking, winner replay и независимые окна оценки. | OPT-03–05, UI-01 |
| OP-28 | Реальные browser race/scaling проверки больших результатов. | UI-03 |
| OP-29 | Сквозные пользовательские E2E и ошибки. | UI-01/02/07 |
| OP-30 | Продуктовые visuals/alerts/logs с lifecycle/resume. | UI-04–06, RUN-05/06 |
| OP-31 | Полный exact library import surface и semantic origin. | LANG-05/06/12, UI-01/02, REL-02 |
| OP-32 | Один independent conformance pipeline и first divergence. | LANG-09–11, BROKER-08, UI-06, §10.6 |
| OP-33 | Полная fault/sandbox/process-family qualification. | INT-04, RUN-07, OPT-02 |
| OP-34 | Doctor, совместимость, установка и проверенные инструкции. | REL-02–05/07/08 |
| OP-35 | Финальная архитектурная целостность восьми owners без дублированной семантики. | §1.3/1.4, INT-01/03, REL-05/06 |
| OP-36 | Доведение актуальных веток/изменений до согласованной поставки. | INT-01, REL-07/08 |

Ни один OP не закрывается только ссылкой на этот mapping. Финальный gate должен проверить соответствующие содержательные требования и primaries.

<a id="sources"></a>
## 13. Точный Git-срез и основания постановки

### 13.1. Входные revisions для согласования

Это текущая точка анализа и интеграционные входы, а не автоматически утверждённый будущий release manifest. Полные hashes фиксируют прочитанные исходники; изменяемые branch/PR страницы следует перечитать перед переносом.

| Компонент | Прочитанный `release/5.0.0rc6` | Pin в OpenPine candidate `00ddf617…` |
|---|---|---|
| OpenPine | `faa62e08eb08a79723d94b881ba0588db27985a9` | Рабочий host `00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7` |
| openpine-contracts | `7ad7de5ba9b3a7f0cfec0f0bc8d7a7f91e98df34` | `7ad7de5ba9b3a7f0cfec0f0bc8d7a7f91e98df34` |
| pine2ast | `1bc8ddb72ae6a30b209b5491622dd386b3ade7f5` | `6dfd47badff5ef5b038dfd48a9fb3d8e762b1836` |
| ast2python | `6b3eaf26d090719f9ebbc8b05326ae041c56b28c` | `17ad5f4b6f9c59cb549f4eb08a2c926561985f16` |
| pinelib | `840d70ee87c35101fab64c8c4a32d00ebed78d1b` | `fcfdab59767103cbc6903c0fa251d895d58b1568` |
| backtest_engine | `8eba0eb7350d5eab1529dbaff96f7bf9ada82572` | `8eba0eb7350d5eab1529dbaff96f7bf9ada82572` |
| marketdata-provider | `b453a2b05bc825884270c81500aa879fc543a208` | `b453a2b05bc825884270c81500aa879fc543a208` |
| optimizer | `762f97e306467f505fc4ade22acaf40577214e24` | `762f97e306467f505fc4ade22acaf40577214e24` |

Основания для release refs: [G36], [G37], [G38], [G39], [G40], [G41], [G42]; для используемых pins — [G02].

OpenPine PR #22 на `00ddf617…` основан на PR #21 `43728eddc5d7f9d33cd996403488e76b0b23f922`, а не непосредственно на release. Оба слоя необходимо учитывать при INT-01. Совпадение отдельного pin с branch HEAD не является проверкой совместимости всего стека [G01]–[G04].

Опубликованные дополнительные входы для содержательного review и интеграции:

| Область | Репозиторий / revision | Оставшаяся работа |
|---|---|---|
| Numeric-input completeness | pine2ast `ee61f253913f08955cd49bc0757619bb41708050`, PR #23 | Согласовать с текущим catalog generator, host inventory и LANG-02; внешнюю полноту не выводить из локального guard. |
| Committed heap closure | pinelib `c0dab08a2b1400b676603d7aaa451164f39439a9`, PR #15 | Интегрировать, проверить compiled/host paths и общий state graph. |
| Committed storage roots | pinelib `5e1688c1af6fba0938413af680d077701f2bca07` | Свести с heap closure, проверить history/slots и atomic restore. |
| OCA policy identity | backtest_engine `1c564254602dd0bd89252fcb9fbd5d0303fd4f7d` | Согласовать code/test delta и проверить public compiled/resume путь. |
| JSON checkpoint admission | backtest_engine `c10eeaeebb4e4fd1c05d03e8dc378825f1baff88` | Интегрировать corruption guards; отдельно реализовать production typed restore. |
| Offline ISO precision | marketdata-provider `d4128e81201f24c0625fa3b178889bbc0fe9cc9a` | Интегрировать CSV/Parquet точность с текущим provider и общим data pipeline. |

Эти revisions не являются готовым объединённым candidate. Наличие одной исправляющей ветки не даёт права заменять ею более свежую release-ветку целиком.

### 13.2. Как установлены оставшиеся работы

Нормативная область взята из предоставленных пользователем Master ТЗ, объединённого ТЗ RC6 и полного языкового задания. Git использован для уточнения границы реализации: прочитаны текущие refs всех восьми репозиториев, host pins, PR metadata, выбранные исходники и diff, execution workflow и актуальные package/catalog/status contracts.

Особенно значимы непосредственно проверенные ограничения: статический request discovery без автоматического loader; generated checkpoint без полного broker/IPC resume; worker capabilities без resume; JSON guard без production typed restore; ограниченная strategy/risk/exit surface; разрыв между host pins и свежими language releases; отдельная metadata-квалификация `sort_field`; отсутствие performance evidence в составе common workflow.

Существующие механизмы проверенной рабочей ветки не поставлены на повторную реализацию. В таких местах остаётся **И/К** либо конкретное расширение до полного product contract.

При этом данный проход **не является новым полным исполнением всех suites**: full native/worker/browser/installed/performance campaigns не запускались, все raw artifacts существующих кампаний повторно не пересчитывались. Поэтому **К** означает необходимую финальную проверку, а не доказанное отсутствие функции. Утверждения о локальных PASS в описаниях PR не заменяют независимо прочитанные результаты и примитивы проверки.

Для host `00ddf617…` API показал успешный workflow `RC6 test-platform self-checks` `37286203182`. Это ограниченный scope и не сертификат всего продукта [G15]. Полнота исполнения должна быть установлена по §10 на конечном согласованном candidate.

### 13.3. Первичные ссылки

Ссылки на исходники закреплены по commit. PR/branch/run metadata являются изменяемыми ресурсами. `RC6_REVIEW_36.md` используется для смысла и нумерации исходных OP, не как доказательство текущего выполнения.

| Код | Основание |
|---|---|
| G01 | Текущие host branches и нормативная release-линия. |
| G02 | Фактически используемые host pins семи библиотек. |
| G03/G04 | Слои текущей host-интеграции PR #21/#22. |
| G05–G08 | Реальные planner/workflow contracts, включая отдельные package/frontend/stabilization и strict/diagnostic paths. |
| G09–G14 | Catalog/publication/import/remaining и открытые numeric/reference работы. |
| G15–G18 | Ограниченная hosted проверка и текущие verification owners/reference. |
| G19–G22 | Reference storage closure и актуальные binary-search/sort-field integration основания. |
| G23–G28 | Конкретные request/offline/checkpoint/worker ограничения и repairs. |
| G29–G31 | Реальная broker surface и OCA repair. |
| G32/G33 | Optimizer boundary и исходная OP-трассировка. |
| G34/G35 | Frontend scripts и исполняемая CI-интеграция. |
| G36–G42 | Прочитанные release refs семи библиотек. |

[G01]: https://api.github.com/repos/s7cret/openpine/branches?per_page=100
[G02]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/docs/RC6_LIFECYCLE_SOURCES.json
[G03]: https://github.com/s7cret/openpine/pull/21
[G04]: https://github.com/s7cret/openpine/pull/22
[G05]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine/verification/execution_plan.py
[G06]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/.github/workflows/rc6-native.yml#L1-L150
[G07]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/.github/workflows/rc6-native.yml#L235-L480
[G08]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/.github/workflows/rc6-native.yml#L480-L710
[G09]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine/verification/rc6_catalog_source_pin.json
[G10]: https://github.com/s7cret/pine2ast/blob/1bc8ddb72ae6a30b209b5491622dd386b3ade7f5/pine2ast/hardening/stage2_10_language_publication.json
[G11]: https://github.com/s7cret/pine2ast/blob/1bc8ddb72ae6a30b209b5491622dd386b3ade7f5/pine2ast/libraries/import_version_matrix.py
[G12]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/verification/stage2-remaining-matrix.json
[G13]: https://github.com/s7cret/pine2ast/pull/23
[G14]: https://github.com/s7cret/pinelib/pull/15
[G15]: https://github.com/s7cret/openpine/actions/runs/37286203182
[G16]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine/verification/execution_ci.py
[G17]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/verification/stage2-current-acceptance.json
[G18]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine/verification/stage_gate.py
[G19]: https://github.com/s7cret/pinelib/commit/5e1688c1af6fba0938413af680d077701f2bca07
[G20]: https://github.com/s7cret/pine2ast/blob/1bc8ddb72ae6a30b209b5491622dd386b3ade7f5/tests/fixtures/stage6_udt_binary_search_default_delta.json
[G21]: https://github.com/s7cret/pine2ast/commit/4b97c8fa9407f2bd16e70446e4f2d313e45c4f7d
[G22]: https://github.com/s7cret/pine2ast/pull/24
[G23]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine/runtime/request_requirements.py
[G24]: https://github.com/s7cret/marketdata-provider/commit/d4128e81201f24c0625fa3b178889bbc0fe9cc9a
[G25]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine/runtime/generated_checkpoint.py
[G26]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine/runtime/worker_capabilities.py
[G27]: https://github.com/s7cret/backtest_engine/commit/c10eeaeebb4e4fd1c05d03e8dc378825f1baff88
[G28]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine/runtime/bulk_result.py
[G29]: https://github.com/s7cret/backtest_engine/blob/8eba0eb7350d5eab1529dbaff96f7bf9ada82572/backtest_engine/core/strategy_capabilities.py
[G30]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine/runtime/strategy_host.py
[G31]: https://github.com/s7cret/backtest_engine/commit/1c564254602dd0bd89252fcb9fbd5d0303fd4f7d
[G32]: https://github.com/s7cret/optimizer/blob/762f97e306467f505fc4ade22acaf40577214e24/README.md
[G33]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/docs/RC6_REVIEW_36.md
[G34]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/openpine-ui/package.json
[G35]: https://github.com/s7cret/openpine/blob/00ddf6178e7937c6d7c80ac2b5b9fae12de7b2c7/.github/workflows/rc6-native.yml
[G36]: https://api.github.com/repos/s7cret/openpine-contracts/git/ref/heads/release/5.0.0rc6
[G37]: https://api.github.com/repos/s7cret/pine2ast/git/ref/heads/release/5.0.0rc6
[G38]: https://api.github.com/repos/s7cret/ast2python/git/ref/heads/release/5.0.0rc6
[G39]: https://api.github.com/repos/s7cret/pinelib/git/ref/heads/release/5.0.0rc6
[G40]: https://api.github.com/repos/s7cret/backtest_engine/git/ref/heads/release/5.0.0rc6
[G41]: https://api.github.com/repos/s7cret/marketdata-provider/git/ref/heads/release/5.0.0rc6
[G42]: https://api.github.com/repos/s7cret/optimizer/git/ref/heads/release/5.0.0rc6

---

**Итоговое требование:** завершить перечисленный остаток как согласованный продукт OpenPine 5.0.0 — с полной заявленной семантикой, реальной интеграцией, воспроизводимой установкой и одной вычисляемой приёмкой. Успех подготовительного слоя не заменяет завершение остальных областей.
