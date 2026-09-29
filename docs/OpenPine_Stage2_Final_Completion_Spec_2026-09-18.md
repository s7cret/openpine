# OpenPine RC6 — полное ТЗ на окончательное завершение этапа 2

**Документ:** `OpenPine_Stage2_Final_Completion_Spec_2026-09-18.md`  
**Редакция:** 1.0, 18 сентября 2026 года  
**Адресат:** Codex / ведущий разработчик и исполнители OpenPine  
**Объём:** полностью этап 2 «Complete Pine Language v1–v6», подэтапы 2.1–2.10  
**Связанные исходные задачи:** OP-02, OP-13, OP-14, OP-16, OP-17, OP-18, OP-19, OP-31  
**Форма выполнения:** одна финальная задача с внутренними рабочими пакетами, общей интеграцией и одной итоговой поставкой.

> Требуется не очередной аудит, не новый план и не ещё один частичный кандидат. Требуется завершить реализацию, подтвердить её исполненными проверками и передать полное согласованное дерево восьми компонентов. Четыре оставшихся номера замечаний — не четыре небольших исправления: они включают полноту каталога, межверсионное исполнение библиотек, независимый builtin oracle и общую приёмку. Ранее исправленные 21 замечание должны остаться закрытыми после интеграции.

**Состояние на входе:** этап 2 не принят; `status = in_progress`, `full_stage2_accepted = false`. Настоящий документ — задание на завершение, а не свидетельство выполненной реализации или успешного нового прогона.

## Содержание

1. [Нормативная база, достоверность и приоритет источников](#s01)
2. [Точная исходная поставка и точка продолжения](#s02)
3. [Цель, объём и границы этапа](#s03)
4. [Неподменяемые правила реализации и приёмки](#s04)
5. [Архитектурные владельцы и сквозные контракты](#s05)
6. [Рабочий пакет F0: восстановление единой базы и окружения](#s06)
7. [Рабочий пакет F1: полный version-exact каталог и historical authority](#s07)
8. [Рабочий пакет F2: полноценные воспроизводимые межверсионные импорты](#s08)
9. [Рабочий пакет F3: полный независимый Builtins Oracle](#s09)
10. [Рабочий пакет F4: общая языковая матрица и сохранение 2.1–2.7](#s10)
11. [Рабочий пакет F5: доказательства, hashes, locks и текущая приёмка](#s11)
12. [Рабочий пакет F6: единый CI точного дерева и реальные пакеты](#s12)
13. [Рабочий пакет F7: финальная интеграция, публикация и архив](#s13)
14. [План работ и распределение исполнителей](#s14)
15. [Определение полного завершения этапа 2](#s15)
16. [Обязательный итоговый отчёт и передача результата](#s16)
17. [Приложение A: все 25 замечаний аудита и критерии повторного закрытия](#appendix-a)
18. [Приложение B: все 16 пунктов remaining-matrix](#appendix-b)
19. [Приложение C: существующие точки изменения в исходниках](#appendix-c)
20. [Приложение D: минимальные машинные контракты и сценарии подмены](#appendix-d)
21. [Приложение E: исходные identities и источники](#appendix-e)
22. [Приложение F: готовая инструкция для запуска работы исполнителем](#appendix-f)

---

<a id="s01"></a>
## 1. Нормативная база, достоверность и приоритет источников

### 1.1. На чём основано задание

| Код | Источник | Роль |
|---|---|---|
| N1 | `ТЗ(2).txt`, «OpenPine RC6 — Master ТЗ для Codex» | Исходные требования, архитектура, §7.2–7.13, правила тестов §15–16 и отчётности §20. |
| N2 | `этап 2.txt`, таблица 2.1–2.10 | Полный состав каждого подэтапа и признаки его завершения. |
| A1 | `AUDIT_OpenPine_Stage2_2026-09-18.md` | 25 обнаруженных замечаний; различает runtime-баги, недореализацию, пробелы доказательств и дефекты тестов. |
| R1 | `openpine/REPAIR_REPORT.md` внутри приложенного ZIP | Отчёт о предыдущем исправлении 21 замечания, четырёх открытых требованиях и ограничениях проверок. |
| R2 | `CONTINUATION_STATUS.md` внутри приложенного ZIP | Подтверждает, что основная поставка не интегрирована с экспериментальным продолжением. |
| S1 | Реальные исходники `openpine/sources/` в приложенном ZIP | Фактическая база реализации, а не утверждения из сообщений. |
| E1 | `openpine/sources/openpine/verification/` и журналы поставки | Существующие матрицы, locks, корпуса, текущий статус и исторические результаты. |
| X1 | `continuation-work/` в приложенном ZIP | Непринятые экспериментальные доработки; подлежат разбору, но не автоматически считаются рабочим кодом. |

Ссылки вида `[N1, §7.13]` ниже указывают на эту таблицу. Точные SHA-256 приведены в приложении E. Документ сам содержит необходимый объём требований этапа 2; обращения к источникам нужны для проверки происхождения, а не для восстановления пропущенной постановки.

### 1.2. Правила интерпретации

Исходные требования N1/N2 важнее старых `accepted` в локальных JSON, названий архивов и текстов прошлых сообщений. Исторический зелёный результат не переносится на изменённое дерево. `R1` сообщает о прошлых проверках, но не заменяет их повторное исполнение на финальной базе.

Указанные в этом ТЗ новые ID тестов, поля схем и рабочие пакеты F0–F7 — **проектные требования к финализации**, а не утверждение, что соответствующие файлы/API уже существуют. Если эквивалентный контракт уже есть, расширить его вместо создания параллельного владельца. Существующие имена артефактов и ключей сохранять либо мигрировать явно.

Точные Pine-правила, которых нет в предоставленных материалах, нельзя допридумывать. В частности, документ **не объявляет заранее допустимыми обе смешанные пары v5/v6**, не устанавливает по предположению историческую доступность `array.binary_search*` и не задаёт без доказательства RSI/MACD/TSI seeds. Установление этих контрактов входит в работу F1–F3.

Различать:

- требование проекта поддержать подтверждённую область языка;
- фактическое поведение текущего OpenPine;
- независимое основание Pine-контракта;
- проектное решение о внутренней архитектуре;
- реально выполненную проверку и её результат.

Не называть внутреннюю реализацию, современную документацию или самосогласованность нескольких путей независимым доказательством исторической семантики.

---

<a id="s02"></a>
## 2. Точная исходная поставка и точка продолжения

### 2.1. База этого ТЗ

Использовать приложенный файл:

```text
OpenPine_Stage2_Continuation_2026-09-18(1).zip
SHA-256: d96ccc7453b56da5fa0b0888c04144af0eb5255e5dd5d9078a511b5798516581
```

В ZIP 5780 записей. При побайтовом сравнении с доступным `OpenPine_Stage2_Repaired_Candidate_2026-09-18.zip` все 5761 его записи сохранены без изменений; добавлены 19 записей продолжения, включая корневой статус. Следовательно:

```text
openpine/             = предыдущая исправленная полная поставка
continuation-work/    = отдельные непринятые материалы продолжения
CONTINUATION_STATUS.md
```

Не считать `continuation-work/` уже применённым патчем. Не считать упоминавшийся в переписке `Integrated_Working_Candidate` базой этого ТЗ без отдельного получения его настоящих байтов, manifest и проверенного diff. Сообщение о прогоне «177 тестов» само по себе не является receipt для данного ZIP.

### 2.2. Факты из приложенного состояния

| Область | Подтверждённая исходная ситуация | Действие исполнителя |
|---|---|---|
| Общая приёмка | `stage2-current-acceptance.json`: четыре критерия не приняты; imports — только same-version subset. | Не начинать с переключения статусов. |
| Ранние исправления | R1 помечает 21 из 25 замечаний исправленными. | Сохранить изменения и перепроверить все 25 пунктов по приложению A. |
| Catalog | Сохранённый отчёт: 1538 symbols, 9228 version cells; 4324 флага `UNVERIFIED`; 2378 forms, 11890 dimension cells, 525 `UNVERIFIED` измерений. | Пересчитать на выбранной базе; это исходные контрольные числа, не потолок полноты. Флаги статусов могут пересекаться. |
| Callable denominator | 2390 строк; сохранённая сверка reviewed lock проходит. | Не сокращать denominator; добавлять найденное отсутствующее с review. |
| Oracle execution plan | 12 групп, 100 обязательных сочетаний `group × variant × path` на один запуск плана. | Сохранить весь план и исполнить его на обеих Python-ветках общей приёмки. |
| Текущие oracle receipts | Сохранённый builtin-index не подтверждает обязательные group/path receipts для той evidence-директории. | Получить новые результаты; `NOT_RUN` не выдавать за численную ошибку или PASS. |
| Основной linker | В `pine2ast/.../libraries/linker.py` есть отказ при несовпадении Pine-версий. | Заменить ограничение полноценным контрактом допустимых сочетаний, а не убрать проверку без замены. |
| Экспериментальный mixed import | `mixed-version-integration.json`: `integration_enabled=false`; ошибка интеграции `receipt dictionary not uniquely identified`. | Не переносить автоматически. |
| Экспериментальные runtime-пробы | `mixed-version-runtime.json`: восемь случаев завершились ошибкой harness `AttributeError: 'tuple' object has no attribute 'artifact'`. | Исправить harness и перезапустить; это не доказанный дефект Pine-семантики и не успешно проверенный импорт. |
| Окружение предыдущих прогонов | Не выполнены необходимые Python 3.11 / protected-worker / frontend / all-build проверки; отмечались missing `build`, `hatchling`, `structlog`. | Подготовить реальное окружение до финальной реализации и приёмки. |

Числа взяты из поставляемых отчётов и проверки структуры архива. При составлении настоящего ТЗ новые полные функциональные suite не запускались. Изменение базы требует нового измерения counts, identities и фактического остатка.

### 2.3. Состав восьми компонентов

```text
openpine
openpine-contracts
pine2ast
ast2python
pinelib
backtest_engine
marketdata-provider
optimizer
```

`openpine-ui` находится в дереве компонента OpenPine; его исходники, lockfile и сборка входят в согласованную приёмку. Нельзя подменить восемь компонентов только тремя языковыми пакетами.

### 2.4. Четыре открытых замечания — верхнеуровневые блоки

| ID аудита | Остаток | Рабочие пакеты |
|---|---|---|
| S2-13 | Полный независимый historical/version-exact контракт, включая CAT-04. | F1, F3, F5. |
| S2-14 | Допустимые mixed-version imports с сохранением семантики библиотек. | F2, F4, F5. |
| S2-15 | Полный независимый builtin oracle, seeds/warmup/NA и все execution paths. | F1, F3, F4, F6. |
| S2-23 | Единая приёмка точного восьмикомпонентного дерева. | F0, F5, F6, F7. |

Открытые пункты нельзя свести к CAT-04, одному примеру `5 / 2`, двум stateful индикаторам и наличию YAML-файла CI. Требуется закрытие всей связанной области требований.

---

<a id="s03"></a>
## 3. Цель, объём и границы этапа

### 3.1. Конечная цель

Получить чётко определённую, реализованную и проверенную поддерживаемую поверхность Pine v1–v6, в которой каталог, producer, compiler, runtime, library resolver, host и установленные пакеты согласованы по одному комплекту исходников. [N1, §7.1–7.13]

Проверять не только успешную компиляцию, но и значения, точные типы, диагностику, порядок вычислений, идентичность ссылок, состояние, восстановление и доступность по версиям.

### 3.2. Полный состав 2.1–2.10

| Подэтап | Что должно быть закрыто целиком | Финальный признак |
|---|---|---|
| 2.1 | Version-exact catalog; qualifiers; inputs, source, zero/false, defaults, bounds, overloads. | Нет необъяснённых обязательных пробелов; producer и target согласованы; положительные/отрицательные пары по версиям. |
| 2.2 | Bool, NA, history, первый бар, offsets, conditional history, fields/returns и version differences. | Runtime и compiled совпадают; bool/числа/NA/null/missing не смешиваются. |
| 2.3 | UDF arguments/defaults/named calls, local state, captures, history, вложенность и callsite identity. | Независимые записанные вызовы; корректные пропуски, повторы в цикле, rollback/restore. |
| 2.4 | Arrays/maps/matrices, nested/shared references, copy, history, var/varip и checkpoint. | Правильный reference graph и lifecycle без потери aliasing. |
| 2.5 | UDT и enum, constructors, fields/defaults, methods, containers, imports и serialization. | Nominal identities не смешиваются; некорректные типы/члены отклоняются. |
| 2.6 | Receiver typing, overload families, forms, defaults, visibility, state и recursion diagnostics. | Декларация выбирается по полному контракту, не только имени. |
| 2.7 | If/switch values; for/while/for-in results; break/continue/empty/nested loops; budget; once lifecycle. | Правильное versioned выполнение; нет обхода лимита; once не ломает rollback/fill-recalculation. |
| 2.8 | Exact reproducible library imports, transitive graph, all exported kinds, version combinations, CLI/API и diagnostics. | Один комплект воспроизводит одну программу; нет соседнего checkout/latest/substitution; зависимости входят в identities. |
| 2.9 | Полный required builtin denominator, независимые expected, overloads/types/NA/warmup/edges/state и все пути. | Полный builtin-index проходит; missing не удалены ради зелёного результата. |
| 2.10 | Один согласованный source set, обе Python, worker, Stage 1, архитектура, frontend, corpus и builds. | Одновременно приняты четыре основных критерия и все дополнительные gates. |

### 3.3. Не переносить в этап 2 целиком другие этапы RC6

Сохранить границы исходного Master ТЗ и existing remaining-matrix:

| Следующий этап | Не требуется полностью реализовывать здесь | Что требуется на границе этапа 2 |
|---|---|---|
| 3: Market Data / `request.*` | Полный discovery/alignment/provider/synthetic-chart layer. | Точный языковой каталог, typing, ABI и корректное делегирование существующему owner; проверить затронутые adapters и не вводить silent fallback. |
| 4: Full lifecycle / full-job resume | Полное восстановление всех внешних broker/IPC/alert/job состояний. | Pine-state rollback/commit/checkpoint, imports identity и реальные worker-path регрессии, необходимые языковому DoD. |
| 5: Strategy / broker | Вся матрица fill/exit/margin/OCA/FIFO и торговая семантика. | Не сломать существующий broker; проверить once/UDF/state при реальных поддерживаемых fill callbacks. |
| 6: Performance / optimizer | Полная оптимизация и benchmark-программа. | Регрессионная корректность, отсутствие явного архитектурного дублирования и неизменность детерминированности. |
| 7: Product UX | Полный redesign, большие графики, все visuals/alerts и полный browser UX. | Существующий frontend green; обычный compile/import/diagnostic путь работает с новым контрактом. |
| 8: Final RC6 release | Закрытие всех 36 OP, полный doctor/migration продукта. | Реальные packages, stage-2 wheel smoke, единый source lock и воспроизводимый архив текущего этапа. |

`HOST_DELEGATED` не означает ни реализованную численную семантику, ни разрешение удалить строку из каталога. Для каждой такой строки нужны точные owner, contract и stage boundary. Отсутствующую обязательную функцию языка нельзя переименовать в «задачу следующего этапа» без основания N1/N2.

---

<a id="s04"></a>
## 4. Неподменяемые правила реализации и приёмки

### 4.1. Правила реализации

1. Не создавать Python-based параллельный Pine parser/typer/evaluator для основной семантики. Не включать regex/`ast.parse`-подмножество из X1 как способ объявить полный mixed-version support.
2. Исправлять semantic owner и сквозной контракт, а не добавлять локальную заплатку только в `input`, CLI, UI, oracle runner или отдельный builtin.
3. Не ослаблять consumer bundle, exact ABI, type evidence, source-lock validation и fail-closed проверки ради принятия ошибочного bundle.
4. Не переписывать исходный `//@version` библиотеки в версию consumer. Не использовать один изменяемый глобальный `pine_version`, протекающий между вызовами/сессиями.
5. Не выбирать missing dependency по latest, похожему имени, соседнему checkout или незафиксированному сетевому результату.
6. Сохранять единственного owner effective configuration и различение zero/false/missing/NA. Не дублировать host/broker/runtime семантику.

### 4.2. Правила тестов

Запрещены как способ получения зелёного результата: `skip`, `xfail`, удаление failing test, сокращение обязательного inventory/`selected_regressions`, сужение fixtures до удачных случаев, расширение tolerance под observed, отключение sandbox/AppArmor/Bubblewrap, подмена protected worker in-process запуском. [N1, §3, §15–16]

Если прежний тест неверен, сохранить исходное падение, показать независимое основание исправления, изменить предположение отдельным коммитом и добавить регрессию. Переименование теста не должно скрывать удаление прежнего сценария.

`collect-only` доказывает состав, но не выполнение. Exit code 0 без полного обязательного набора, пустой JUnit, stale XML, отсутствующие receipts или тесты из другого SHA не являются PASS.

Отрицательный тест, который **исполнен и подтвердил предусмотренный контрактом отказ**, является PASS. Но тест, ожидающий отказ там, где функция должна работать, не считается реализацией этой функции.

### 4.3. Правила независимости

`OpenPine direct == OpenPine compiled` и «v5-результат совпадает с v6-результатом» — полезные дифференциальные/метаморфические проверки, но не независимый Pine oracle.

Разрешённые N1 источники expected: независимый ручной расчёт, математическая библиотека с проверяемой формулой, фиксированный внешний TradingView export. Не требовать TradingView export там, где контракт корректно закрывается первыми двумя способами; не выдавать их за TradingView verification. Для исторической доступности одной математической формулы недостаточно.

### 4.4. Правила статусов

```text
inventory integrity != contract completeness
contract completeness != numerical conformance
BOUND != Pine-compatible
EXAMPLES_ALL_PATHS != full contract accepted
same-version subset != imports accepted
runner exists != tests executed
ZIP CRC/checksum != semantic acceptance
merge != stage accepted
```

При реально недоступном обязательном внешнем основании или окружении фиксировать конкретный blocker и выполненные попытки. Такой результат не соответствует заказанному полному завершению; его нельзя автоматически принять по исключению. Ни этот документ, ни исполнитель не дают разрешения молча сократить объём.

---

<a id="s05"></a>
## 5. Архитектурные владельцы и сквозные контракты

### 5.1. Ответственность компонентов

| Компонент | Ответственность в финализации | Что ему не передавать |
|---|---|---|
| `pine2ast` | Parser, semantic admission, versioned catalog, qualifiers, source-bound symbols/types/overloads и library linking/provenance. | Исполнение численных Pine-функций как второй runtime; ослабление типов под возможности target. |
| `ast2python` | Consumer validation, IR/lowering, точные call bindings, emitted program, source maps, сохранение origin/version metadata. | Самостоятельный альтернативный каталог или собственные формулы Pine вместо runtime owner. |
| `pinelib` | Runtime operators, values, state/history, references, version policies, ABI и direct/stateful builtins. | Host resolution, UI, сетевой поиск библиотек, broker bookkeeping. |
| `openpine` | Orchestration, обычные API/CLI, effective config, data/worker adapters, cache/artifact boundary, verification orchestration. | Дублирование Pine arithmetic/type semantics или broker engine. |
| `openpine-contracts` | Разделяемые схемы и публичные межкомпонентные контракты в рамках существующего ownership. | Вторые преобразователи и скрытые неверсированные transport shortcuts. |
| `backtest_engine` | Existing broker и callbacks для language lifecycle integration. | Перенос в него Pine state owner. |
| `marketdata-provider` | Existing deterministic data/metadata boundaries, необходимые тестам. | Необоснованная подмена data snapshots live-ответами. |
| `optimizer` | Сохранение isolation/determinism и существующих интеграционных контрактов. | Общий изменяемый semantic context между trial. |

### 5.2. Сквозные identities

Программа должна воспроизводимо зависеть как минимум от исходных байтов, версии Pine, catalog identity, semantic policy identity, ABI/target identity, exact library graph, compiler options и влияющих host capabilities. Использовать уже существующие механизмы hash/provenance и расширить их там, где теперь участвует несколько semantic origins.

Для импортированных сущностей необходимо различать:

```text
source module / library revision
source checksum
source Pine version
declaration identity
resolved overload / call form
nominal type identity
written callsite identity
transitive dependency graph identity
```

Конкретное представление выбирается архитектурным решением. Обязательные свойства: неизменяемость после admission, детерминированная сериализация, проверка consumer/runtime, включение влияющих данных в artifact/cache/checkpoint compatibility. См. F2 и приложение D.

---

<a id="s06"></a>
## 6. Рабочий пакет F0 — восстановление единой базы и окружения

**Цель:** до изменения семантики получить одну прослеживаемую базу и доступные обязательные контуры проверки.

### F0.1. Инвентаризация входа

Распаковать ZIP в новый каталог с защитой от выхода путей за его пределы. Проверить CRC, SHA-256 входного файла, root manifest и source locks существующими верификаторами. Сохранить исходную поставку read-only или как неизменяемый baseline.

Составить reconciliation register:

| Поле | Требование |
|---|---|
| `path`, component | Каждый изменённый/добавленный/удалённый файл. |
| base identity | Hash файла в repaired baseline. |
| experimental identity | Hash версии в X1, если существует. |
| remote identity | Реальный commit/tree/hash, только если Git-источник получен. |
| decision | `keep`, `port`, `merge`, `reject`, с основанием. |
| proof | Связанные requirements, tests и результат review. |

В `continuation-work/` отдельно рассмотреть `version_compatibility.py`, `linker.py`, version-matrix tests, `run_exact_stage2.py`, package builder и workflow template. Не перезаписывать более свежий основной файл полной экспериментальной копией. Ошибку тестового harness исправить до использования результатов X1.

### F0.2. Согласование с Git

Нормативная линия N1 — `release/5.0.0rc6`. При наличии Git-доступа выполнить fetch и прочитать реальные HEAD, `RC6_LIFECYCLE_SOURCES.json`, stage statuses и candidate commits/tags. Не использовать SHA из старой переписки как текущий.

Сопоставить архив и текущие ветки **каждого** компонента. Сохранить полезные свежие изменения с обеих сторон; не заменять ветку целиком архивом. Зафиксировать, какая база выбрана и какие функциональные изменения добавлены при reconciliation.

Само это ТЗ не разрешает удалять пользовательские данные или произвольно переписывать release history. Запрещены force push и непрозрачный squash всего этапа. При архивном режиме commit history/patches должны быть передаваемы, даже если remote publication не выполняется.

### F0.3. Обязательное окружение

Подготовить реальные Python **3.11 и 3.13** в отдельных изолированных окружениях. Зафиксировать полные patch-версии, platform, зависимости и build backend. Установить объявленные dev/runtime extras компонентов, а не подменять отсутствующие зависимости заглушками.

Проверить до основного прогона:

- `pytest`, нужные explicit plugins, `build`, setuptools/wheel и `hatchling` для соответствующих backend;
- `structlog` и остальные объявленные host dependencies;
- deterministic provider extras, предусмотренные зафиксированным CI-профилем;
- Node/npm по фактическому UI lockfile/конфигурации, TypeScript/Vitest и build dependencies через штатную установку;
- настоящий protected-worker host, Bubblewrap, действующие профили ограничений и отдельный worker identity по имеющейся реализации;
- доступность места для clean builds, wheel install, snapshots, corpus outputs и полного evidence bundle.

Preflight обязан проверять запуск, а не только `which`: Python version; импорт нужных зависимостей; минимальный protected subprocess; фактическое применение sandbox; frontend toolchain. Недоступность инфраструктуры устраняется подготовкой подходящего разрешённого окружения, а не отключением защиты.

### F0.4. Базовый прогон и сверка inventory

Сохранить initial collect-only inventory и его hash, затем выполнить исходный обязательный набор, чтобы отделить прежние ошибки от новых. Старые counts в R1 — ориентир, не разрешение объявить новый run выполненным.

Фиксировать timeouts, setup errors, plugin failures, crashes и недостающие selectors. Для известного host inventory additive-review проверить прежние node IDs и ранее добавленные 17 audit-тестов; не вводить правило «любой superset разрешён».

**Выход F0:** baseline reconciliation; environment receipt; полный установленный mandatory inventory; реальные исходные failures; одна рабочая база; экспериментальные файлы разобраны. Предварительный PASS F0 не означает приёмку языка.

---

<a id="s07"></a>
## 7. Рабочий пакет F1 — полный version-exact каталог и historical authority

**Закрывает:** S2-13; CAT-01–04; каталогическую часть S2-15; полноту 2.1.  
**Основной owner:** Pine2AST; согласование с AST2Python, PineLib и OpenPine verification.  
**Нормативное основание:** N1 §7.2, N2 2.1; A1 S2-11–13, S2-16.

### F1.1. Независимый знаменатель, а не перечень того, что уже установлено

Построить и зафиксировать требуемую поверхность для Pine v1–v6 из версионно применимых оснований. Текущие 1538 symbols / 2390 callable rows нельзя считать доказательством исчерпывающего внешнего каталога. Сравнивать внешний/нормативный набор с producer и target в обе стороны.

Каждая внешняя обязательная сущность должна иметь одну прослеживаемую запись или явную связь с canonical записью. Различать aliases, canonical forms, overloads и разные source spellings; не смешивать количество записей с количеством функций.

Не переносить автоматически API/параметры/defaults v6 назад в v1–v5. Отсутствие записи в одном кратком источнике не доказывает отсутствие функции в версии. Историческое переименование не должно уничтожать прежнее публичное имя или создавать ложную новую функцию.

### F1.2. Полный контракт строки

| Измерение | Обязательные данные |
|---|---|
| Идентичность | Stable symbol ID, public spelling, namespace, kind: function/method/type/constant/variable; canonical/alias relation. |
| Версия | Pine version, применимость источника, availability, deprecation и semantic changes. |
| Форма вызова | Function/method/namespace-call, receiver position/type, variadic/generic правила, exact overload identity. |
| Параметры | Порядок, точные имена, types/qualifiers, required/optional, допустимость named/positional, duplicate/unknown handling. |
| Defaults | Наличие default отдельно от его значения; `0`, `false`, пустая строка, NA и отсутствие не взаимозаменяемы. |
| Возврат | Точный scalar/tuple/reference/nominal type, qualifier, shape, nullable/NA-политика, если применима. |
| Admission | Допустимая область типов, qualifiers и значений; ограничения length/index/enum/format; compile/runtime error boundary. |
| Runtime owner | Direct, delegated или истинно unavailable по версии; target binding и причины делегирования. |
| Основание | Источник, раздел/фрагмент, датировка/версия, hash snapshot, какое именно утверждение подтверждается. |
| Проверки | Positive/negative/version test IDs, binding test, oracle case IDs; актуальные receipts отдельно от декларации покрытия. |

Статусы N1 сохранить: `AVAILABLE`, `UNAVAILABLE`, `DEPRECATED`, `CHANGED_SEMANTICS`, `HOST_DELEGATED`, `RUNTIME_DIRECT`, `UNVERIFIED`. Если в существующей модели это независимые флаги, не превращать их в взаимоисключающий enum без явной миграции.

### F1.3. Требования к authority

Для каждой обязательной версии/сигнатуры связать конкретное основание с утверждениями: наличие символа; callable shape; types/qualifiers; defaults; изменение поведения. Одной ссылки на главную страницу справочника недостаточно.

Приоритет конкретного доказательства выше его общего названия. Версионный snapshot/reference или явное migration/release описание может подтверждать availability/signature. Независимая формула подтверждает вычисление, но не дату появления функции. Современная страница без исторической применимости не подтверждает v1/v2/v4.

Зафиксировать конфликтующие источники, точный предмет конфликта, выбранное решение и review. До разрешения обязательного конфликта строка остаётся blocker. Нельзя закрыть её `UNAVAILABLE` только потому, что сведения не найдены, или `VERIFIED` потому, что реализация уже есть.

Результатом исследования должен быть воспроизводимый реестр доказательств, пригодный для offline-проверки. Полные сторонние материалы сохранять только в пределах прав использования; достаточны датированная ссылка, разрешённый фрагмент/структурированное утверждение и его provenance. Нельзя фабриковать snapshot, дату проверки или внешний export.

### F1.4. CAT-04: `array.binary_search*` в v4

Закрыть **две независимые части**:

1. Версионная доступность точных функций/форм/типов в v4 и смежных версиях.
2. Поведение поиска, включая absent-neighbor cases, в версиях, где функции действительно доступны.

После установления контракта проверить: пустой массив; один элемент; найденный элемент; duplicates; значение до первого/после последнего/между элементами; отрицательные числа; int/float по допустимым overloads; boundaries; NA и неправомерные элементы, если релевантно; требования к сортировке; неразрешённые типы/qualifiers. Expected должны содержать точные индексы/NA/ошибки по независимому основанию, а не результат текущего `pinelib`.

Если функция отсутствует в версии, сохранить строку с подтверждённым `UNAVAILABLE` и исполняемым negative admission test. Если присутствует — реализовать и прогнать обязательные пути. Любая смена availability проходит catalog/ABI/callable-lock delta review; молчаливое удаление v4 запрещено.

### F1.5. Producer → consumer → target

Для каждой обязательной исполняемой формы проверить:

```text
public Pine syntax
→ resolved symbol/overload/call-form
→ full argument type/qualifier evidence
→ consumer bundle admission
→ exact target/ABI binding
→ runtime behavior
```

В `actual_type` / `expected_type` должны передаваться содержательные конкретные типы, включая generic element/nominal identity. Не заполнять пропуски произвольным `any`. При отсутствии легальной точной привязки compilation должен fail closed; затем устранить root cause, если форма входит в обязательную область.

Сравнивать producer-admitted domain с target-admitted domain. Один проход `const int` не закрывает `series float`; positional smoke не закрывает named/default overload; новая функция не считается готовой по одному имени в runtime.

### F1.6. Полнота и целостность gate

Раздельно рассчитывать и показывать:

- целостность inventory и отсутствие необъяснённых пропусков;
- полноту обязательных dimensions и независимых оснований;
- согласованность producer/consumer/target;
- независимую семантическую проверку, относящуюся к F3.

Для Stage 2 mandatory surface не должно оставаться `UNVERIFIED` по требуемым availability/signature dimensions. Строки будущих этапов остаются в общем каталоге с конкретным owner/scope; это не уменьшает global denominator и не делает непроверенное поведение совместимым с TradingView.

Обязательные mutation-тесты: удалить параметр, default, return type, qualifier, overload; поменять version availability; потерять alias; подменить source evidence; добавить external symbol без producer mapping; оставить binding только для более узкого типа. Gate должен обнаруживать каждую мутацию, а не проверять словарь по ключам, которые сам же создал.

### F1.7. Артефакты и выход

Расширить существующие `stage2-1-authority.json`, version-exact matrix, catalog packs/generator, target manifest, callable lock и delta reviews. Не редактировать только сгенерированный JSON в обход генератора.

**Критерий выхода F1:** для всего required каталога v1–v6 установлены применимые контракты; CAT-04 разрешён; нет обязательных неопределённых dimensions; producer/target согласованы; все positive/negative/version/mutation tests исполнены; catalog identities и reviewed locks соответствуют выбранному дереву. Это закрывает `versioned_catalog` только вместе с соответствующими общими receipts, а не по локальному boolean.

---

<a id="s08"></a>
## 8. Рабочий пакет F2 — полноценные воспроизводимые межверсионные импорты

**Закрывает:** S2-14; IMPORT-01–04; 2.8 и связанные части 2.2–2.7.  
**Владельцы:** Pine2AST linker/admission, AST2Python IR/emission, PineLib semantic policies/state, OpenPine compile/artifact boundary.  
**Нормативное основание:** N1 §7.11, N2 2.8; A1 S2-14; X1.

### F2.1. Сначала — подтверждённая матрица сочетаний

Составить полный decision table для consumer Pine v1–v6 × library Pine v1–v6. Для каждого сочетания указать:

```text
consumer_version
library_version
library/import syntax applicability
allowed / rejected_by_language / unresolved
exact authority reference
applicable export kinds
boundary conversion rules
positive or negative test IDs
```

Синтаксически недопустимые версии покрываются negative tests, а не фиктивными исполняемыми библиотеками. Для v5/v6 отдельно разрешить вопрос каждого направления. Нельзя автоматически утверждать, что разрешены обе смешанные пары; нельзя объявить обе запрещёнными только потому, что нынешний linker их отвергает.

Все разрешённые языком сочетания должны быть реализованы, а запрещённые — диагностироваться определённо. `unresolved` в обязательной матрице не позволяет принять imports. Same-version путь должен сохранить работоспособность.

### F2.2. Архитектурное решение: semantic origin не теряется при linking

Подготовить ADR с текущей цепочкой parse/link/type/lower/emit/runtime и точкой потери version context. В основной базе lexical module lowering происходит до последующего admission; простое снятие проверки версии не обеспечивает сохранения исходной семантики.

Обязательное решение независимо от конкретной реализации:

1. Каждый модуль разбирается и проверяется в своём исходном Pine context.
2. Декларации, выражения и выбранные builtins сохраняют origin module/version/contract identity после linking и lowering.
3. Compile-time folding/default evaluation использует контракт того исходного выражения, которое вычисляется.
4. Runtime выполняет origin-bound операцию по соответствующей политике, не по случайно текущей глобальной версии root script.
5. Argument/return boundary отдельно применяет подтверждённые правила совместимости типов/qualifiers версий caller/callee.
6. Semantic metadata входит в artifact и проверяется consumer/runtime, а не служит неподтверждённым advisory field.

Допустим immutable per-module IR/semantic policy либо эквивалентное source-bound lowering. Конкретный механизм согласовать с существующим ownership. Запрещены переписывание `//@version`, строковый поиск отдельных операторов и отдельный Python-evaluator для части библиотек.

### F2.3. Версионно-зависимые области, обязательные для разборки

| Область | Что проверить внутри библиотеки и на границе вызова |
|---|---|
| Arithmetic | Integer/float division с учётом const/non-const контекста; `%`; promotion; большие int; precision и rounding. |
| Bool / NA | Исторические/v6 bool rules, typed NA, coercion, defaults, comparison, ternary/if/switch и function returns. |
| Evaluation order | Lazy/eager логические ветви и побочные effects/stateful calls; невычисленный operand не создаёт state/history. |
| History | First/missing history, offsets, local/callsite history, skipped calls, imported UDF. |
| Loops | Версионно различающиеся bounds/evaluation; break/continue; tuple/reference results и callback budget. |
| Builtins | Namespace/availability/overload/defaults по версии библиотеки; seed/warmup/type qualifier, а не root version. |
| Nominal types | UDT/enum declarations, fields/defaults, constructors, copy и version availability. |
| Methods | Receiver-bound overload, private/public, namespace/receiver-dot forms, declaration-local version. |
| Persistence | var/varip по полям и references, rollback/commit/checkpoint в origin module. |

Список — обязательные области исследования, не утверждение о том, что у каждой есть различие во всех версиях. Для каждой области зафиксировать «различается / совпадает / неприменимо» с основанием и тестом. Недоказанное совпадение не считается совместимостью.

Диагностический пример с `5 / 2` обязан быть проверен как минимум для констант и параметров нужных типов/qualifiers. Ожидания предварительно получить независимо. Один положительный пример деления не принимает F2.

### F2.4. Type и value boundary

Не превращать прежнее `bool na` в `false` только для успешного crossing. Не подменять Pine NA транспортным `null`. Не терять `int` на неоправданном float round-trip. Если значение или exported type не может законно пересечь конкретную boundary, выдать предусмотренный контрактом отказ с source coordinates.

Проверить scalar, tuple, array, map, matrix, UDT и enum аргументы/возвраты в тех сочетаниях версий, где они доступны. Поддержать named/default arguments и точные qualifier constraints. Недопустимые crossing combinations должны иметь независимое объяснение, а не общий отказ «mixed unsupported».

### F2.5. Callsite, state и reference graph

Written callsite identity должна сохраняться после projection/linking. Два разных записанных вызова импортированной UDF имеют независимое state; повторные итерации одного записанного вызова не создают новый callsite. Alias, nested call и транзитивная цепочка не должны случайно сливать или размножать state.

Для exact module/revision номинальная идентичность должна воспроизводиться детерминированно. Одноимённые типы разных библиотек и ревизий не объединять по строковому имени. Diamond graph не должен случайно дублировать единственную exact dependency или терять различия реальных ревизий. Связь module identity, declaration identity и written callsite отразить в ADR и тестах.

References, переданные через boundary, должны сохранять разрешённое aliasing, copy depth и per-field varip. Нельзя сериализовать всё как скаляры, клонировать shared object при каждом вызове или делать весь UDT `varip` из-за одного поля.

### F2.6. Scoped execution без протекания контекста

Вложенная цепочка root → library A → library B должна применять semantic origins соответствующих выражений. После возврата, exception, abort, cancellation и checkpoint restore root и другие sessions получают свой контекст без изменений.

Проверить два чередующихся session/trial с разными версиями и зависимостями. Глобальная изменяемая версия runtime, process-wide singleton policy и cache key без module context запрещены. При использовании scoped frame его вход/выход должен быть exception-safe; persisted state должен хранить нужную identity, а не ссылаться на текущий ambient context.

### F2.7. Exact resolver, locks и публичный путь

Сохранить и дополнить:

- exact revision/checksum admission, transitive dependencies, cycle detection и deterministic resolution;
- отсутствие latest/similar-name/sibling-checkout fallback;
- aliases, same-name libraries, public/private visibility, all exported functions/methods/UDT/enum;
- неизменность admitted lock после передачи вызывающей стороной;
- source maps в оригинальную библиотеку, её revision, файл, строку/колонку, declaration/callsite;
- обычный CLI/API compile с library lock и source set — без тестового private injection;
- отсутствие сырого внутреннего `AttributeError` на корректных пользовательских вызовах.

Missing revision, checksum mismatch, inaccessible private symbol и cycle должны завершаться различимыми структурированными ошибками. Source diagnostics не должны ссылаться только на сгенерированную объединённую строку.

### F2.8. Artifact/cache/checkpoint invalidation

Изменение любого влияющего фактора — dependency bytes/revision, transitive graph, module Pine version, semantic profile, selected overload, nominal registry или ABI — должно инвалидировать artifact/cache и несовместимый checkpoint.

Смена только транспортного alias не должна случайно менять семантику; требования к identity такого изменения зафиксировать явно и проверить детерминированность. Одинаковые exact inputs после повторного resolver/compile должны давать согласованную canonical identity.

Restore в несовместимый library graph не допускается молча. При миграции формата bump schema/version; либо строго отклонить старый checkpoint, либо реализовать отдельно проверенную migration. Существующие hashes нельзя сохранить ценой исключения нового version metadata.

### F2.9. Обязательные серии тестов

| Серия, проектный ID | Минимальный состав |
|---|---|
| `S2FIN-IMP-VERSION` | Все reviewed consumer/library пары; positive для allowed, negative для запрещённых, wrong-version constructs внутри library. |
| `S2FIN-IMP-SEMANTICS` | Области F2.3; сравнение с независимыми expected, а не только native-vs-import self comparison. |
| `S2FIN-IMP-TYPES` | Scalars/tuple/collection/UDT/enum crossing; defaults; qualifiers; same-name nominal types; private type leakage. |
| `S2FIN-IMP-GRAPH` | Nested and diamond graph, same-name modules/revisions, missing exact revision, cycle, checksum tamper, no latest. |
| `S2FIN-IMP-STATE` | Два written calls, loop repeats, skipped calls, nested UDF/methods, history, field varip, shared/copy references. |
| `S2FIN-IMP-LIFECYCLE` | Historical, realtime ticks, rollback, commit, JSON checkpoint/restore, interruption на границе library call. |
| `S2FIN-IMP-ISOLATION` | Interleaved sessions, concurrent jobs/trials по поддерживаемой архитектуре, exception-safe возврат контекста. |
| `S2FIN-IMP-CONSUMER` | Обычные API/CLI и wheel-only smoke; оригинальные diagnostics; admitted immutable lock. |
| `S2FIN-IMP-TAMPER` | Мутация module version/origin/overload/dependency identity в artifact и checkpoint вызывает отказ. |

Library resolution использует воспроизводимые exact inputs, а не зависящее от времени discovery. Сетевой registry, если существующая архитектура его использует на отдельном этапе подготовки, не должен менять already-locked semantics.

### F2.10. Выход F2

Допустимая version matrix подтверждена; все разрешённые комбинации реализованы для обязательного exported surface; запрещённые имеют точные negative tests; source-bound semantics сохранена на folding/lowering/runtime/state/restore; API/CLI и packaging paths работают; X1 интегрирован либо аргументированно заменён без второго semantic owner.

Нельзя закрыть F2 формулировкой «поддержаны stateless scalar libraries» или «убрана проверка версии». `imports = accepted` разрешён только при выполнении полной матрицы и общем source-bound CI.

---

<a id="s09"></a>
## 9. Рабочий пакет F3 — полный независимый Builtins Oracle

**Закрывает:** S2-15; STATE-03/04; BUILTIN-01–04; численную часть CAT-04; 2.9.  
**Основной owner:** independent conformance/oracle; исправления — у настоящих catalog/compiler/runtime owners.  
**Нормативное основание:** N1 §7.12–7.13, N2 2.9; A1 S2-06–10 и S2-15.

### F3.1. Что считается полным oracle

Требуется не максимальное количество примеров, а покрытие всего обязательного callable denominator по версиям, overloads, фактической области типов/qualifiers и поведению. Успешные несколько значений одной функции не принимают остальные перегрузки.

Для каждой строки поддерживаемого Stage 2 builtin surface нужны: конкретный contract; независимые expected; assignment на exact overload/form; исполнение всех применимых обязательных путей; проверка state/NA/warmup/errors; полные receipts. `BOUND`, `RUNTIME_DIRECT`, `EXAMPLES_ALL_PATHS` и успешный corpus-run являются разными характеристиками и не заменяют друг друга.

Сохранить все 2390 исходных callable rows и найденные дополнительные строки. Любую осмысленную коррекцию aliases/duplicates оформить reviewed mapping с сохранением трассировки прежней записи, а не использовать как скрытое уменьшение scope.

### F3.2. Источники expected и происхождение

Сохранить различение `manual_fixture`, `tradingview_export`, `missing`. Если используются независимые расчёты математической библиотекой, явно записывать derivation method/dependency/version в рамках выбранной и документированной схемы; не маркировать такой результат внешним TradingView export.

Каждый fixture должен содержать или однозначно ссылаться на:

```text
case_id and contract/requirement IDs
Pine version, exact overload/call-form
original Pine source and its hash
input data/events, instrument/settings and their hashes where relevant
independent source/derivation and version applicability
expected values/types/NA/error/state transitions
numeric tolerance and rationale
required execution paths
exact assignment identity
```

Expected формируются до сравнения с observed. Код независимого derivation не импортирует OpenPine/PineLib/compiler или обёртку над ними, не читает текущие observed и не меняет формулы по расхождениям. Метаморфические и self-differential tests хранить как дополнительные, с отдельным типом доказательства.

`tradingview_verified=true` допустим только для реально полученного и зафиксированного внешнего TV результата с matching source/data/settings/version. Не требовать этого флага для принятого manual fixture; общий Stage 2 accepted не означает, что каждый его тест проверен внешним TV.

### F3.3. Обязательный evidence plan на входе

Во всех следующих группах зафиксированы пять путей:

```text
abi
compiled_historical
compiled_realtime
compiled_rollback
compiled_checkpoint
```

| Точный ID группы | Корпус в `verification/` | Зафиксированные variants | Сочетаний variant/path |
|---|---|---|---:|
| `builtin-array-concat` | `builtin-array-concat-v1` | `full`, `compact` | 10 |
| `builtin-array-operations` | `builtin-array-operations-v1` | `full`, `compact` | 10 |
| `builtin-event-history` | `builtin-event-history-v1` | `full`, `compact` | 10 |
| `builtin-map-operations` | `builtin-map-operations-v1` | `full`, `compact` | 10 |
| `builtin-matrix-operations` | `builtin-matrix-operations-v1` | `full`, `compact` | 10 |
| `builtin-numeric-closure` | `builtin-numeric-closure-v1` | `full`, `compact` | 10 |
| `builtin-recursive-ta` | `builtin-recursive-ta-v1` | `full`, `compact` | 10 |
| `builtin` | `builtins-v1` | `legacy` | 5 |
| `builtin-rolling_statistics` | `builtin-rolling-statistics-v1` | `legacy` | 5 |
| `builtin-scalar` | `builtin-scalars-v1` | `legacy` | 5 |
| `builtin-string-operations` | `builtin-string-operations-v1` | `full`, `compact` | 10 |
| `builtin-transcendental` | `builtin-transcendentals-v1` | `legacy` | 5 |
| **Всего на один исполненный plan** | **12 групп** | **20 group/variant сочетаний** | **100** |

ID с `_` и `-` сохранять точно. `legacy` здесь — имя исторического формата evidence, не разрешение включать запрещённый legacy runtime fallback. `full/compact` не равны режимам `bulk/interactive`: последние проверяются отдельной интеграционной матрицей.

План требуется выполнить на Python 3.11 и 3.13 при одном candidate identity. Для текущего неизменённого плана это 100 group/variant/path обязательств на каждую Python-ветку, не «100 unit-тестов». Это исходный минимум; если новые contracts требуют новых групп/variants/cases, plan расширяется с review. Старые обязательства не вычитаются.

### F3.4. Assignment и first divergence

Каждый observed case должен проверяться против своего exact expected, версии, аргументов, overload/form и corpus hash. Нельзя прикрепить любой прошедший пример из группы ко всем её callable rows.

При расхождении выводить first divergence: case/contract/version, path, event/bar/tick/callsite, expected и observed, точные типы, NA representation, tolerance, state/heap delta и источник исходного Pine. Итоговая средняя ошибка или финальное совпавшее значение не должны скрывать ранний неверный warmup.

Обнаруживать orphan fixture, assignment к отсутствующему case, duplicate assignment, ошибочную overload identity, перепутанные versions, stale plan/corpus hashes и запуск другой variant под нужным именем.

### F3.5. Полнота контрактов, кроме обычных значений

| Семейство | Обязательные измерения и границы |
|---|---|
| Operators / conversions | Знаки, int/float/bool separation, precision больших целых, zero divisor, NA/null/missing, rounding ties, qualifiers и version differences. |
| Numeric/stateless | Domain boundaries, zero/negative/large/small inputs, nonfinite transport rejection, return type, tolerance, допустимые variadics/defaults. |
| Rolling/statistical | Length min/invalid/variable, первые бары, NA в начале/середине/конце окна, flat/impulse/alternating sequence, window eviction и version applicability. |
| Recursive TA | Exact seed и warmup, update order, NA continuation, skipped calls, resume, длинная recurrence и accumulated tolerance. |
| Event/history | First/missing occurrence, occurrence index, first bar, отсутствующий/пропущенный ряд, вызов внутри UDF/branch/loop. |
| Arrays | Empty/singleton, generic element types, variadic promotion, from/sum/search, duplicates, index errors, slices/copies/aliasing/NA и mutation state. |
| Maps | Key/value types, enum/nominal keys где допустимо, missing key, replacement, iteration/order contract, aliasing и restore. |
| Matrices | Shape/index/type, empty/boundaries, numeric operations по заявленному catalog, rows/references, iteration/copy и invalid shape. |
| Strings | Пустая строка, positions/bounds, replacement occurrence/defaults, named parameters, split return type, Unicode там, где входит в контракт, enum title conversion. |
| Colors | Component extraction/defaults, alpha/transparency, typed NA/replacement, допустимые color overloads и type errors. |
| Delegated functions | Точная language/ABI boundary и настоящий host owner по scope; не фальшивый local numeric expected для отсутствующего следующего этапа. |

Таблица не заменяет перечень функций. Исполнитель строит contract coverage из полного denominator, а затем применяет необходимые измерения к **каждой** форме. Новая непокрытая family добавляется в plan; на отсутствие её в этой таблице нельзя ссылаться как на исключение.

### F3.6. STATE-03: RSI/MACD v1–v2

Установить независимые version-applicable правила: формула, инициализация используемых сглаживаний, первый валидный выход, требуемая история, NA в источнике, длины/defaults и qualifiers. Нельзя выводить v1/v2 только из документации v3 или копировать современное имя/пространство имён назад.

Сохранить пошаговый reference calculation на компактном наборе, включая intermediate state; внешний export — только если реально получен. Затем расширить набор: возрастающий, убывающий, плоский, чередующийся и импульсный источник; короткая история; NA/gaps; length boundaries; skipped UDF calls.

Проверить не только последний MACD tuple/RSI value, но и все warmup markers, промежуточные бары, типы, nested UDF callsite и state после rollback/checkpoint. Выполнить пять путей plan. Неподтверждённый seed остаётся blocker, даже если steady-state близок.

### F3.7. STATE-04: TSI startup / flat / NA

Отдельно установить: инициализацию обеих цепочек сглаживания; обработку изменения цены и абсолютного изменения; поведение числителя/знаменателя на startup и flat source; первые валидные значения; пропущенные данные и допустимые lengths.

Нужны independently derived cases для zero/flat source, первого изменения после flat warmup, одиночного импульса, разнонаправленных изменений, NA в разных фазах и resume. Не выбирать «удобный нулевой прогрев» по фактическому результату тестируемого runtime.

Reference state и expected freeze должны предшествовать runtime fix. На всех пяти путях сопоставлять результаты и committed/pending state согласно контракту, а не только итоговое отношение.

### F3.8. Смысл каждого execution path

| Путь | Что действительно требуется исполнить |
|---|---|
| `abi` | Настоящий admitted direct ABI вызов с точными argument/return contracts. |
| `compiled_historical` | Исходный Pine → producer → compiler → runtime на последовательности historical bars; не вызов direct функции под другим именем. |
| `compiled_realtime` | Несколько обновлений одного неподтверждённого бара и commit; stateful effects и outputs в нужной фазе. |
| `compiled_rollback` | Изменить pending state, выполнить rollback/abort, повторить tick и сравнить с независимой ожидаемой траекторией. |
| `compiled_checkpoint` | Сериализовать настоящий checkpoint, создать новый runtime/экземпляр, восстановить и продолжить; проверить типы и reference graph. |

Stateless builtin всё равно проверяется в зафиксированных compiled lifecycle paths: отсутствие состояния не отменяет правильность compiler/host invocation. Неприменимость отдельного измерения должна быть обусловлена language contract и машинно зафиксирована; существующий обязательный path нельзя просто заменить `N/A`.

### F3.9. Численная точность и равенство

Exact сравнение обязательно для integers, bool, enum/nominal identities, strings, indices, lengths, missing/NA markers и структурных свойств. Для float tolerance задаётся по функции/overload и независимому основанию.

Предлагаемое стандартное правило для конечных float, если контракт не требует иного:

```text
abs(observed - expected) <= atol + rtol * abs(expected)
```

`atol`/`rtol` не назначать единым произвольным числом всему каталогу. Для recursive TA обосновать допуск длиной последовательности и способом независимого расчёта. Для неоднозначных rounding/sign-zero случаев сначала установить контракт. NA не считать совпавшим с транспортным null, Python NaN или отсутствующим полем.

### F3.10. Выход F3

Все обязательные callable contracts имеют независимые ожидаемые результаты и exact assignments; STATE-03/04 и численные CAT-04 закрыты; 12 исходных групп и все расширения полностью исполнены по locked plan; `builtin-index` не показывает обязательных missing/unverified/partial paths; first-divergence и tamper detection проверены.

`independent_builtin_expected = accepted` выставляется по полному покрытию контрактов и реальным source-bound receipts, а не по одному `all_declared_runs_passed=true`, если сам plan неполон.

---

<a id="s10"></a>
## 10. Рабочий пакет F4 — общая языковая матрица и сохранение 2.1–2.7

**Цель:** довести весь этап 2 до единого языкового результата, не сломав уже реализованные части при закрытии F1–F3.  
**Закрывает:** `stateful_language_matrix`; STATE-01/02; регрессии всех 25 замечаний.  
**Основание:** N1 §7.3–7.10, §15; N2 2.1–2.7.

### F4.1. Матрица применимости

Для каждого требуемого сценария фиксировать:

```text
requirement/substage
Pine version(s)
source module version(s), if imported
value/type/qualifier category
call context: top-level / UDF / method / loop / branch / imported declaration
lifecycle: historical / realtime / rollback / commit / checkpoint
execution mode: direct / compiled / relevant host bulk / interactive
positive/negative expectation
exact test/case ID and execution receipt
```

Не требовать несуществующий enum/UDT синтаксис в версии, где он недоступен: там нужен negative version test. Но область «применимо» определяется независимым контрактом, а не текущими ограничениями OpenPine. Выборочная пара сочетаний не доказывает полный контракт остальных.

Для обязательных взаимодействий version × callsite × reference × lifecycle должна быть явная прослеживаемая проверка. Генерация cases разрешена; генерация expected самим runtime — нет. Старые substage matrices сохранить как историю и связать с новой current execution matrix, не копируя `accepted` из старых Python/SHA.

### F4.2. Подэтап 2.1: qualifiers и inputs

Проверить все заявленные input families, включая `source`, `active`, defaults, bounds/options и зависимые expressions. Точные параметры и версия появления берутся из F1. Обязательно:

- const/input/simple/series admission в допустимых местах; producer и target принимают одинаковую заявленную область;
- named/positional/default формы, unknown/duplicate arguments, обязательность параметров;
- сохранение `0`, `false`, пустой строки, явного NA и отсутствия значения как разных состояний;
- integer precision на bounds и вычислении defaults; отсутствие float round-trip для exact integer calculations;
- единая Pine семантика folding, `input`-expressions и runtime, включая `%` и typed comparisons;
- неизменяемость admitted input specification, вложенных expressions и source bindings после передачи вызывающей стороной;
- version-negative tests параметров/overloads, не существующих в ранней версии;
- обычный API/UI input path без скрытой подстановки default при явном zero/false.

Нельзя объявить input family закрытой только потому, что UI строит форму.

### F4.3. Подэтап 2.2: bool, NA и история

Полностью проверить first bar, missing history, zero/positive/invalid offsets, условно вычисляемые ряды, `bool` поля, local/returned bool, `if/switch` results и legacy/v6 различия.

Обязательные регрессии S2-01–04: signs и zero-divisor `%`; bool/number equality/inequality в folding/runtime; большие signed integers и отрицательное деление; прямое сравнение с литералом `na` против корректного обращения с типизированной переменной, содержащей NA.

Сравнивать точный type/value representation. Нельзя использовать Python truthiness вместо Pine typing. Проверить `na(x)` и остальные допустимые обращения отдельно от отрицательных literal-comparison cases. Host serialization не должна превращать missing/NA/bool/zero друг в друга.

### F4.4. Подэтап 2.3: UDF и состояние вызовов

Полная поверхность: parameters/defaults, named/positional calls, locals, var/varip, history, nested calls, recursion diagnostics, shadowing, legal global captures и stateful builtins inside UDF.

Сценарии:

1. Два записанных вызова одной UDF на одном баре имеют независимые state histories.
2. Один записанный вызов в нескольких итерациях цикла не создаёт новые callsites; порядок обновлений соответствует контракту.
3. Пропуск вызова через branch не маскируется фиктивным update или глобальной history.
4. Вложенная UDF/method/import сохраняет выбранную декларацию, origin policy и state identity.
5. Rollback повторяет разрешённую committed траекторию, varip переживает только предусмотренные фазы, checkpoint восстанавливает тот же logical state.
6. Неоднозначные/недопустимые captures и recursion корректно диагностируются в оригинальном источнике.

Добавить stateful TA в UDF, смешанный импорт и interrupted invocation; не ограничиваться stateless add-function.

### F4.5. Подэтап 2.4: arrays/maps/matrices и references

Проверить declarations/constructors, допустимые element/key/value types, параметры/returns, copies, slices где поддерживаются, shared и nested references, mutation, reference history, var/varip и heap serialization.

Обязательны graph assertions: два alias указывают на один object; независимая copy не превращается в alias; вложенная копия имеет ту глубину, которую предписывает контракт; после JSON checkpoint и нового runtime graph identity восстановлена. Простого сравнения содержимого недостаточно.

Для `varip` учитывать ограничения типов и полей по версии. `varip` одного поля UDT не делает весь объект персистентным на tick. Rollback коллекции не должен терять shared references или создавать новые state slots.

Сохранить сквозные `array.from`/`array.sum` регрессии: variadic arity/types, promotion, empty/NA, slice/reference аргументы и matrix row. Сам `matrix for-in` и стандартные builtin calls проверяются раздельно, чтобы binding failure не маскировался диагнозом «сломался итератор».

### F4.6. Подэтап 2.5: UDT и enum

UDT: declaration, constructors/defaults, field read/write, allowed default syntax, nested references, copy, fields with different persistence, arrays/maps containing UDT, args/returns, history/rollback/checkpoint.

Не допускать произвольные default expressions/calls/global captures в тех случаях, где Pine-контракт запрещает их. Сохранять разрешённые literal/builtin/enum формы согласно версии и проверенному catalog/admission.

Enum: declaration/members, nominal inference, comparisons, args/returns, switch, UDT fields, library export/import, serialization/checkpoint и `str.tostring` title. Unknown member, wrong enum type, private/public leakage и wrong version должны иметь negative tests.

Отдельно два типа `Point` и два enum с одинаковым spelling из разных exact libraries. Не объединять nominal types по имени или одинаковому набору полей. Проверить imported titles и callback/restore identity.

### F4.7. Подэтап 2.6: methods и overloads

Поддержать UDT и допустимые collection receivers, одинаковые имена разных receiver types, function/method families, receiver-dot/namespace формы где доступны, defaults, named parameters, returns, visibility, exported methods и independent state.

Resolution использует receiver type, аргументы/qualifiers, version, exact declaration и scope. Повторный выбор «по имени» в runtime запрещён. Проверить ambiguity, wrong receiver, duplicate/default-related ambiguity, private method, declaration-bound recursion и same-name imported types.

Обязательны не только negative ambiguity tests, но и успешные разные overloads, вызываемые в одном source, UDF и imported graph.

### F4.8. Подэтап 2.7: if/switch и loops as values

Проверить if/switch returned values; `for`, `while`, `for-in` arrays/maps/matrices; scalar/tuple/reference/UDT/enum results. Обязательны last completed result, zero iterations, break, continue, nested loops и version-dependent bound evaluation.

Тест tuple-result должен действительно возвращать tuple из цикла и присваивать результат. Отдельные `a := ...; b := ...` внутри loop не являются таким тестом. Сохранить старый scalar-assignment scenario отдельно. Проверить соседние blocks/DEDENT и комбинации tuple/control после parser fix S2-25.

Nested loops, UDF loops и imported loops используют общий предусмотренный callback iteration budget. Нельзя сбрасывать budget на вложенности или переходе между библиотеками. Проверить точно на границе лимита, превышение и fail-closed error; контрольный допустимый loop должен завершаться.

### F4.9. Полная матрица `once`

Проверить lexer/AST/typing/lowering/runtime state, top-level/UDF/method/branch/loop/import contexts и все lifecycle transitions из N1 §7.10.

Обязательный сценарий S2-24 должен начинать **первое выполнение once на неподтверждённом tick**, а не после его завершения на историческом баре. Затем: pending effects → rollback/abort → replay → commit → настоящий checkpoint JSON → новый runtime → restore → продолжение. Каждая фаза должна иметь assertions по outputs и state/completion slot.

Отдельные реальные integration cases: historical fill recalculation; `calc_on_order_fills`; `process_orders_on_close`; несколько fill callbacks там, где поддерживается existing host; once внутри функции, вызванной при перерасчёте.

Нельзя закрыть эту матрицу переводом completion slot в `varip`, отключённым `if False` checkpoint, mock receipt или повторным тиком уже навсегда завершённого once. Runtime bug утверждается только по воспроизведению, а не по отсутствию теста.

### F4.10. Выход F4

Все 2.1–2.7 представлены current matrix с версионной применимостью; новые source-bound imports не ломают local и same-version paths; stateful categories реально проходят historical/realtime/rollback/checkpoint; все закрытые ранее контрпримеры покрыты; новый полный inventory сохраняет прежние обязательства.

`stateful_language_matrix = accepted` требует общего прогона этой матрицы на финальном candidate. Локальный PASS семи отдельных поднаборов разных деревьев не заменяет его.

---

<a id="s11"></a>
## 11. Рабочий пакет F5 — доказательства, hashes, locks и текущая приёмка

**Цель:** результат приёмки вычисляется по действительным артефактам точного candidate, а не устанавливается руками.  
**Закрывает/сохраняет:** S2-11/12/16/17/19/20/22/23; 2.10.

### F5.1. Один текущий статус

Использовать существующий `verification/stage2-current-acceptance.json` как единственный current verdict либо выполнить явную schema migration с совместимыми readers. Не создавать новый «ещё более финальный» status file, после которого прежние commands читают другое состояние.

Согласовать с ним:

```text
verification/stage2-progress.json
verification/stage2-remaining-matrix.json
verification/stage2-remaining-matrix-lock.json
verification/stage2-10-language-publication.json
verification/stage2-source-lock.json
verification/stages.json
root DELIVERY_MANIFEST.json
publication metadata трёх языковых пакетов
README и итоговый отчёт
```

Исторические matrices/receipts сохраняются неизменяемыми либо с явным historical label и ссылкой на original copy. Исторический `accepted=true` допустим только как результат своего scope/tree/run, не как текущий итог. CLI и документация обязаны отличать его от current verdict.

### F5.2. Exact source lock восьми компонентов

Lock должен охватывать все исполняемые исходники, тесты, schemas, catalog packs/generators, ABI, build metadata, CI/gate config, dependency locks, frontend source/lock и fixtures/expectations, влияющие на результат. Для каждого компонента: source origin/revision при наличии Git, path, tree/content digest и список/счётчик файлов по канонической политике.

Нельзя удостоверять три языковые библиотеки и выдавать это за восемь компонентов. Нельзя использовать архивное дерево для local tests, а устаревшие sibling Git pins — для final CI. Реально checked-out bytes во всех jobs должны совпадать с coordinated source lock.

Uncommitted или внеархивный код, `PYTHONPATH` на соседний checkout, неподписанная замена fixture и mutable библиотечный cache не должны влиять на финальный запуск.

### F5.3. Исключить циклическое хеширование

Реализовать и документировать ациклическую схему identities. Требуемые свойства:

```text
immutable executable/test inputs + plan + inventory + expected
→ candidate input identity
→ built artifact identities
→ execution receipts bound to candidate/artifacts
→ aggregated current acceptance receipt
→ delivery manifest and archive checksum
```

Generated receipts могут находиться в явно определённых output paths и не входить в hash **входов исполнения**; при этом входят в manifest итоговой поставки. Сам lock не хеширует собственное поле checksum. Нельзя исключить из source identity весь `verification/`, каталог, runner, schemas или публикационный JSON, который реально влияет на исполнение/validation.

Expected plan/inventory и проверяющий их код замораживаются **до** run. Статус, вычисленный после run, не должен изменять semantic inputs. Если финальная правка embedded metadata меняет входы, пакеты или проверяемый контракт, это новый candidate: нужные builds/tests повторяются, а прежний PASS не переносится.

Выбранная политика должна позволять выполнить финальную упаковку без бесконечного изменения source hash вслед за receipt. Решить это архитектурно, не произвольным списком исключений после падения hash-проверки.

### F5.4. Минимум настоящего run receipt

| Поле | Требование |
|---|---|
| Candidate/source identity | Точный входной lock; source hash до/после run. |
| Команда | Полный argv, cwd, selection/profile; секреты в env редактировать, влияющие параметры сохранять. |
| Среда | Python/Node/OS/toolchain, dependency lock/freeze hash, worker profile. |
| Scope | Component/suite, required inventory hash, corpus/plan/assignment hashes, execution path/variant. |
| Исполнение | Run/job/attempt IDs, start/end timestamps, returncode, timeout/crash/cancel status. |
| Результат | Executed node/case IDs; tests/passed/failures/errors/skips; first divergence при наличии. |
| Файлы | Относительные пути к JUnit/log/observations/worker receipts и их реальные hashes. |
| Проверяемость | Указанные файлы существуют, парсятся, совпадают по identities и соответствуют именно этому run. |

Count в summary не заменяет JUnit/case outputs. Receipt не может состоять из `passed=true` и произвольного source hash. Старая запись stdout, скопированная в новую директорию, должна быть выявлена несоответствием run identity/plan/source.

### F5.5. Publication verification

Сохранить исправление S2-19: local/partial observation явно сообщает неполноту, coordinated требует все версии/catalog/compiler/runtime identities. Отсутствующий actual нельзя пропускать и возвращать обычный полный `published`.

При `full_stage2_accepted=true` verifier обязан требовать одновременно:

- четыре основных критерия `accepted`;
- отсутствие незакрытых обязательных residuals, в том числе из expanded catalog/oracle;
- полный обязательный plan/inventory и настоящие clean execution receipts;
- совпадение source/corpus/ABI/catalog/package identities;
- Python 3.11 и 3.13, real worker, Stage 1, architecture, frontend, builds и no-shrink gates.

Непустой residual register не является доказательством их закрытия. Resolved historical residuals допустимы только со structured status, resolution evidence и областью применимости. Empty register также не считается самостоятельным доказательством полноты.

### F5.6. Tamper и негативная матрица verifier

Обязательны исполняемые проверки следующих подмен:

| Мутация | Обязательный исход |
|---|---|
| `full=true` при одном непринятом критерии | Отказ. |
| `full=true` при незакрытом CAT-04/STATE-03/STATE-04/import/CI blocker | Отказ. |
| Нет runtime/compiler observation в coordinated mode | Отказ; local mode показывает incomplete. |
| Удалён suite/path/variant/case из обязательного плана | Отказ no-shrink/plan-lock, не уменьшенный знаменатель. |
| Пустой JUnit, неизвестный node ID, duplicate, missing mandatory node | Отказ. |
| JUnit/observations/expected/source содержимое изменено после hash | Отказ. |
| Receipt для другого Python/SHA/ABI/library graph | Отказ. |
| Source изменился во время или после финального run | Новый candidate, прежняя приёмка неприменима. |
| В относительный receipt path подставлены traversal/symlink escape | Отказ. |
| Green stdout при returncode!=0/timeout/crash | Отказ. |
| `published=true` без обязательной wheel/resource проверки | Отказ соответствующего coordinated gate. |
| Отключён sandbox либо вместо worker запущен in-process путь | Отказ; такой receipt не считается protected execution. |

### F5.7. Выход F5

Все hashes/locks/canonical files согласованы; не осталось актуальных stale references; independent expected не переименованы в TV; current verdict единственный и вычисляемый; любые предусмотренные подмены вызывают отказ. Положительный gate работает на полном валидном наборе — нельзя создать verifier, который всегда отказывает и считать его проверенным только по negatives.

---

<a id="s12"></a>
## 12. Рабочий пакет F6 — единый CI точного дерева и реальные пакеты

**Закрывает:** S2-23 и дополнительные критерии N1 §7.13.  
**Требование:** одна согласованная кампания приёмки immutable candidate. Параллельные jobs разрешены; смешение произвольных исторических деревьев — нет.

### F6.1. Полный план запусков

| Gate ID | Что запустить | Где / обязательность |
|---|---|---|
| G00 | Preflight, environment identity, source reconciliation/lock check. | Перед основной кампанией и в каждом независимом job. |
| G01 | Inventory collection и no-shrink по exact selectors. | Обе Python; все соответствующие компоненты. |
| G02 | Полные обязательные suites contracts, Pine2AST, AST2Python, PineLib. | Python 3.11 и 3.13. |
| G03 | Полные обязательные suites backtest_engine, deterministic provider, optimizer и утверждённый host inventory. | Python 3.11 и 3.13. |
| G04 | Все targeted regressions A1, новые F1/F2/F3/F4 и verifier mutations. | Обе Python, где код/контракт применим. |
| G05 | Catalog/authority/contract/ABI/assignment/remaining gates. | Обе Python на одном catalog/target/plan. |
| G06 | Все builtin groups × variants × пять paths. | Python 3.11 и 3.13; исходный минимум 100 path obligations на каждую. |
| G07 | Stateful language и imports matrix через соответствующие direct/compiled/host пути. | Обе Python; actual lifecycle execution. |
| G08 | Real sandbox и protected workers, mandatory host bulk/interactive integration. | Обе Python в подготовленном защищённом окружении. |
| G09 | Неизменённый Stage 1 frozen corpus и архитектурные/capability gates. | Обе Python по утверждённому foundation profile. |
| G10 | Lint, imports, compileall, schema/API compatibility по принятому обязательному набору. | Для изменённых owners и существующих обязательных checks. |
| G11 | Реальные wheel+sdist всех восьми компонентов, включая Hatchling backend. | Build/test compatibility с Python 3.11/3.13; exact build inputs. |
| G12 | Clean wheel-only installation и публичные smoke/metadata/imports checks. | Отдельная чистая среда каждой Python. |
| G13 | Frontend dependency install, tests, build, Node package tests и backend contract checks. | Exact `openpine-ui` tree/lock; backend schema из той же кампании. |
| G14 | Source unchanged, full evidence aggregation, current DoD calculation. | После завершения всех обязательных jobs. |
| G15 | Round-trip распаковка финального ZIP и проверка manifest/source/evidence/package consistency. | На фактическом архиве, перед передачей. |

«Полный host inventory» означает весь зафиксированный обязательный RC6 набор, включая `rc6_tests` и `selected_regressions.json`, а не удобную произвольную выборку. Если discovery показывает отсутствующие обязательные тесты этапа 2, inventory расширить. Не нужно произвольно включать в language DoD все live-service тесты, которые исходный deterministic profile обоснованно отделяет; исключения не расширять и отражать явно.

### F6.2. Настоящий worker и sandbox

Проверить не только установку Bubblewrap, но и фактическую изоляцию worker: предусмотренные user/permissions, restrictions, process boundary и доступ только к разрешённым inputs. Тестировать положительный исполняемый Pine job и отрицательные попытки нарушить sandbox-контракт.

Нужны реальный worker startup, protocol handshake/capabilities, run, error/cancel/timeout paths согласно обязательному inventory, exit/cleanup и receipts. Protected worker не заменяется monkeypatch, fake subprocess, in-process runtime или заранее записанным JSON.

Historical/realtime/rollback/checkpoint через compiler должны дойти до соответствующего host/worker пути там, где это требует общая матрица. На одинаковых admitted inputs сравнить обещанные результаты bulk/interactive; не считать разные transport envelopes семантическим расхождением и не выбрасывать пропавшие обязательные outputs.

Если job host не позволяет применить требуемый sandbox, использовать разрешённый подходящий runner. Отказ окружения — не дефект Pine-кода, но остаётся незакрытым обязательством приёмки до реального запуска.

### F6.3. Stage 1 и архитектура

Не менять frozen Stage 1 corpus/expected ради новых результатов. Сохранить checksums, inventory и ownership tests. При выявленном regression исправить affected owner, а не переписать foundation.

Проверить единственность `openpine.runtime.rc6_config` effective configuration boundary, capability graph, exact ABI, no legacy fallback, no duplicate semantic owners и first-divergence reporting. Новый mixed import механизм не должен нарушать эти гарантии.

### F6.4. Пакеты: сборка и содержимое

Собрать реальные **wheel и sdist всех восьми компонентов** штатными build backends в чистой копии exact sources. Сам факт наличия ZIP/wheel extension не является сборкой.

Обязательно проверить S2-18 regression:

```text
pine2ast/hardening/stage2_10_language_publication.json
ast2python/admission/stage2_10_language_publication.json
```

Включить также соответствующие PineLib publication/ABI/resources, каталоги, schemas и новые необходимые module policy/import metadata. Сверять содержимое установленных resources с source/build manifest.

Проверить build-from-sdist: из распакованного sdist можно собрать wheel с обязательными resources без скрытого исходного checkout. Изменения package-data/MANIFEST и генераторов должны согласовываться, а не только вручную добавлять файлы в готовый wheel.

### F6.5. Clean installed-package checks

Для каждой Python создать новое окружение; установить полученные wheels и объявленные зависимости. Запускать из каталога вне source checkout, без editable install, без `PYTHONPATH`, без sibling sources. Зафиксировать `__file__`/package origins и полный installed distribution set.

Проверить:

- загрузку publication metadata, catalog/ABI и coordinated identity verification;
- обычный compile API/CLI с language source и exact library lock;
- same-version и allowed mixed-version smoke с independently frozen expectations;
- serialization/restore и nominal identity по доступному публичному контракту;
- диагностируемый отказ на missing/bad exact dependency;
- отсутствие случайного импорта production module из тестового checkout;
- работоспособность runtime/compiler boundaries после установки, не только в source tree.

Если tests нужно запускать снаружи, копировать только нужный test/corpus harness; не добавлять рядом production source packages, способные затенить wheels.

Полную автономную wheelhouse всех сторонних зависимостей не считать автоматически обязательной новой задачей этапа 8. Однако установочные dependencies/versions должны быть зафиксированы и доступность установки реально проверена. Не заявлять offline-ready, если сторонние зависимости в поставку не включены.

### F6.6. Frontend

На точной поставке выполнить штатные команды из `openpine-ui/package.json` и lockfile. Для входного workflow это установка через `npm ci`, frontend tests, production build и `test:node`; проверить актуальные scripts после reconciliation, не выдумывать несуществующий CLI.

Node tests, которые требуют настоящий `dist`, запускаются после build. OpenAPI/backend schema берётся из того же candidate/job family, а не из старого artifact. Существующие обязательные UI checks должны быть зелёными; новые public compile/library diagnostics и inputs отображаются без потери source location/version/error cause.

Новый полный browser redesign/E2E всего продукта остаётся этапом 7. Если изменение F2 затронуло уже имеющийся обязательный browser check, он включается и не пропускается.

### F6.7. Требования к runner / workflow

Развить существующий verification entrypoint и orchestration без второго competing DoD. `run_repair_checks.py --suite full` в исходной поставке не является полным eight-component acceptance runner. Экспериментальный `run_exact_stage2.py` не считается готовым только по имени или unit-тестам.

Runner должен:

1. Получать exact candidate, expected plan/inventory identities, output directory и профили окружения.
2. Проверять реальную версию каждого интерпретатора и источники установленных пакетов.
3. Запускать нужные команды, сохранять stdout/stderr, JUnit, case results, timestamps и exit codes.
4. Не проглатывать errors/timeouts и не интерпретировать непустой лог как PASS.
5. Завершать зависимые checks явным `blocked/not_run` при отсутствующих prerequisites, но не скрывать их из итогового denominator.
6. Сохранять доказательства даже при failures; чистить дочерние процессы по timeout без удаления журналов.
7. Сопоставлять все outputs с candidate/plan/corpus/ABI/inventory и проверять отсутствие изменений inputs.
8. Возвращать ненулевой exit code, если хотя бы одно обязательное требование не закрыто.
9. Иметь unit/integration/mutation tests самого runner, включая пустой/stale/malformed JUnit и изменённое дерево.

Одна кампания может выполняться несколькими CI jobs/attempts с одинаковым immutable candidate. Фиксировать связь всех attempts и не скрывать прежние падения. После изменения кода/fixtures/plan/packages новый candidate требует повторной полной финальной кампании; случайный набор старых PASS не складывается в accepted.

### F6.8. Команды и воспроизводимость

В README итоговой поставки должны быть **реально существующие и проверенные** команды: preflight; inventory; полный stage2 run; evidence aggregation/verify; package build/install check; archive verify. При изменении CLI обновить tests и документацию.

Существующие `python -m openpine.verification stage1`, `builtin-index`, `stage2-remaining` и их точные аргументы брать из кода/workflow. Новый full-stage entrypoint — задача реализации, а не фиктивная команда, которую можно просто написать в отчёте.

**Выход F6:** все обязательные G00–G14 реально зелёные на одном candidate; нет mandatory skipped/errors/failures; все source/plan/ABI/package identities совпали; полный evidence bundle существует. YAML и настроенная среда без запуска не закрывают F6.

---

<a id="s13"></a>
## 13. Рабочий пакет F7 — финальная интеграция, публикация и архив

### F7.1. Одна интегрированная поставка

Все принятые изменения должны находиться в основном дереве `openpine/sources/`. Запрещено передать прежний repaired archive плюс каталог `continuation-work/` и назвать это завершённым этапом. Промежуточные patches/experiments можно сохранить в истории с явной маркировкой, но они не заменяют интегрированный product code.

Перед seal выполнить independent review final diff: semantic owners, exact locks, version matrix, oracle independence, mandatory inventory, актуальность generators/build metadata и совместимость публичных API. Каждое замечание A1 и каждый пункт remaining-matrix должны иметь итоговый проверяемый статус.

### F7.2. Git и история изменений

Коммиты должны быть функционально понятными: architecture/schema decision; catalog/authority; mixed-version implementation по owners; oracle/fixtures; tests; integration/CI; packaging/publication. Не складывать все изменения в один непрозрачный commit. Сохранить уже имеющиеся исправления и review evidence.

При работе с remote проверять фактические SHA после push/merge и связывать их с source content lock. Не считать GitHub Actions зелёным для архивного diff, который не попал в checked-out commits. Нормативную release history не переписывать; временные ветки архивировать/удалять только по политике N1 и в пределах разрешённых действий.

Публикация/merge не принимают этап автоматически. Финальный receipt должен относиться к реально поставляемым исходникам. Если после CI изменился executable/test/package input, требуется новая финальная кампания.

### F7.3. Минимальный состав полного архива

```text
openpine/
  README.md
  FINAL_STAGE2_REPORT.md
  DELIVERY_MANIFEST.json
  <проверенный entrypoint проверки поставки>
  <проверенный entrypoint полной stage2-приёмки>
  sources/
    openpine/
    openpine-contracts/
    pine2ast/
    ast2python/
    pinelib/
    backtest_engine/
    marketdata-provider/
    optimizer/
  evidence/
    baseline-and-reconciliation/
    authority-and-oracle-provenance/
    python-3.11/
    python-3.13/
    workers/
    frontend/
    packages/
    final-acceptance/
    failures-and-retries/
  dist/
    <реальные wheel и sdist восьми компонентов либо явно сопоставленный dist-layout>
  history/
    <нужные исходные отчёты, reviews и непринятые эксперименты с явным статусом>
```

Это логический состав, не обязательство создать параллельные каталоги вместо существующих. Существующие `repair-evidence` / `delivery-evidence` могут сохраняться с однозначным current/history mapping. В любом layout README должен сразу указывать current receipt и команды.

В архив не включать secrets, tokens, приватные credential caches, рабочие venv/node_modules, ненужные промежуточные build directories и неверифицируемые внешние symlinks. Не удалять полезную историю без review, но не оставлять два конкурирующих «основных» дерева.

### F7.4. Финальная проверка реального ZIP

Проверка выполняется на **созданном архиве**, а не только до упаковки:

1. Проверить zip CRC, список файлов, отсутствие path traversal/неожиданных symlinks и восемь компонентов.
2. Распаковать в новый независимый каталог.
3. Запустить поставляемый verifier; сверить source input lock, delivery manifest, current receipt, expected/plan/inventory и hashes evidence/package files.
4. Выполнить document-command smoke и clean installed-package smoke на упакованных artifacts; не обращаться к исходной рабочей директории.
5. Подтвердить, что status/README/report и machine receipt совпадают; старые history locks не выданы за новый verdict.
6. Вычислить SHA-256 окончательного ZIP и отдать его с архивом. Любая последующая модификация ZIP требует нового checksum и round-trip verify.

Повторять весь полный functional CI после простого архивирования не требуется, если round-trip доказывает побайтовую идентичность всех уже испытанных immutable inputs/artifacts. Но smoke не заменяет отсутствующую полную кампанию и не позволяет принять изменённые после неё inputs.

### F7.5. Выход F7

Один полный интегрированный архив, проверенная распаковка, реальные восемь package pairs, воспроизводимые команды, точный source lock, настоящие общие receipts и подробный отчёт. Название `Final`/`Accepted` допустимо только после DoD из раздела 15. Не выдавать ссылку на файл, которого фактически нет.

---

<a id="s14"></a>
## 14. План работ и распределение исполнителей

### 14.1. Рабочие пакеты — не новые пользовательские этапы

F0–F7 выполняются внутри **одной задачи окончательного завершения этапа 2**. Между ними допустимы targeted tests, небольшие merges и внутренние review. Не завершать работу после очередного F-пакета предложением начать следующий отдельным запросом, если доступно продолжение текущей задачи.

| Порядок | Пакет | Зависимости / смысл |
|---|---|---|
| 1 | F0 | Сначала реальная база, инструменты и возможность обязательного CI. |
| 2, параллельно | F1 + проектирование F2 + foundations F5/F6 | Establish contracts и provenance; инфраструктура не откладывается до конца. |
| 3 | Реализация F2 по owners | На подтверждённых contracts и согласованном semantic-origin ADR. |
| 4, параллельно | F3 + F4 | Независимые expected и runtime fixes; регрессии всей language matrix. |
| 5 | F5 | Заморозить правильные catalogs/ABI/expected/plan/inventory, мигрировать current status. |
| 6 | F6 | Полная кампания на одном frozen candidate, не только targeted checks. |
| 7 | Исправление всех найденных regressions | При изменении inputs новый candidate и повтор final campaign. |
| 8 | F7 | Independent review, единая поставка, round-trip, финальный отчёт. |

### 14.2. Роли

Сохранить схему N1: Lead + семь specialist roles, одновременно не более четырёх specialist agents. Это план организации работ, не требование к конкретной модели/провайдеру или обещание параллельного исполнения.

| Роль | Основная ответственность |
|---|---|
| Lead / integrator | Scope, ADR, ownership, conflicts, identities, inventory, final DoD и единая поставка. |
| Catalog / Version Matrix | F1, historical applicability, CAT-04, catalog generator и negative pairs. |
| Reference / UDT / Enum | F4 references/nominal types и F2 boundary contracts. |
| Runtime State | PineLib policies, arithmetic/NA/state, seeds/warmup fixes, lifecycle. |
| Compiler | AST2Python consumer/IR/emission/source origins/call binding. |
| Library Imports | Pine2AST linker/store, transitive resolution, module/overload/source identities и API integration. |
| Independent Oracle | F3 formulas/expected/provenance/assignments; не подгонять expected под implementation. |
| Integration / failure matrix | F0/F5/F6/F7, workers, packages, frontend, locks, tamper tests и receipts. |

Не выдавать двум агентам одновременное владение одними semantic files. Межкомпонентные schema changes сначала согласовать; implementation branches должны иметь компактный scope и проходить targeted review до integration.

Автор implementation не должен единолично принимать независимость oracle и полноту собственного final gate. Lead или отдельный reviewer проверяет raw evidence, а не только summary другого агента.

### 14.3. Минимум по каждому пакету

Каждый пакет возвращает: изменённые owners/files; закрытые requirement IDs; реальные positive/negative/version/regression результаты; stateful evidence где применимо; изменённые catalog/ABI/inventory identities; нерешённые зависимости; commit references. Количество коммитов и количество тестов не являются процентом готовности языка.

---

<a id="s15"></a>
## 15. Определение полного завершения этапа 2

### 15.1. Четыре основных критерия

| Критерий | Необходимое содержание `accepted` |
|---|---|
| `versioned_catalog` | Полный required v1–v6 каталог и версионные контракты, authority coverage, qualifiers/defaults, producer/target consistency и negative pairs. |
| `stateful_language_matrix` | Все применимые 2.2–2.7 и stateful interactions реально исполнены, включая callsite/reference/once/rollback/checkpoint и сохранение прежних исправлений. |
| `imports` | Подтверждённая матрица allowed/rejected версий, полная required exported surface, origin semantics, exact resolver/dependencies/diagnostics и artifact/restore identities. |
| `independent_builtin_expected` | Полный required denominator, независимые expected с attribution, exact assignments, STATE-03/04, пять путей всех обязательных групп/variants и contract-level coverage. |

### 15.2. Обязательные дополнительные условия

- [ ] Python 3.11: полный mandatory inventory исполнен без failures/errors/skips.
- [ ] Python 3.13: полный mandatory inventory исполнен без failures/errors/skips.
- [ ] Source lock охватывает одну согласованную базу всех восьми компонентов и UI.
- [ ] Все обязательные sandbox и реальные protected-worker проверки прошли.
- [ ] Stage 1 frozen corpus не изменён и проходит; architecture/capability gates проходят.
- [ ] Existing mandatory provider/optimizer/broker/host integration не сломаны.
- [ ] Frontend tests/build/Node contract checks проходят на согласованной backend schema.
- [ ] Реальные wheel+sdist всех восьми компонентов собраны; clean install проверен на обеих Python.
- [ ] Все необходимые publication/catalog/ABI/schema/import resources входят в packages и доступны после установки.
- [ ] Test inventory/selected regressions/locked oracle plan не сокращены; все изменения рассмотрены явно.
- [ ] Нет duplicate semantic owners, silent unsupported, legacy fallback или обхода exact admission.
- [ ] Все 25 A1 findings повторно закрыты либо подтверждённо исправлены с актуальными receipts; нет открытых обязательных regressions.
- [ ] Все 16 remaining items пересмотрены по полному критерию, а не старому subset label.
- [ ] Нет обязательных `UNVERIFIED`, `missing`, `partial`, `NOT_RUN`, unresolved authority в принятой области этапа 2.
- [ ] Gate проверяет существование/хеши/содержимое receipts и отклоняет подмену status/source/corpus/plan.
- [ ] Финальный архив после распаковки содержит ровно испытанные inputs/artifacts и согласованный current verdict.

### 15.3. Вычисляемое правило принятия

Логическая спецификация, не замена валидатору:

```text
full_stage2_accepted =
    all_four_criteria_accepted
    AND complete_required_scope_is_proven
    AND all_mandatory_tests_executed_clean_on_3_11_and_3_13
    AND real_protected_worker_and_sandbox_pass
    AND stage1_and_architecture_pass
    AND frontend_and_real_packages_pass
    AND exact_source_plan_inventory_artifact_identities_match
    AND all_required_receipts_exist_and_verify
    AND no_open_mandatory_findings_or_residuals
    AND no_forbidden_acceptance_shortcuts
    AND final_delivery_roundtrip_pass
```

Только после вычисления этого результата текущая приёмка может содержать:

```text
status = accepted
full_stage2_accepted = true
```

До этого — `in_progress`, `full_stage2_accepted=false`. `blocked_by_external_information` или `blocked_by_environment` — диагностические причины незавершённости, а не альтернативный зелёный статус этапа. При успешной приёмке не требуется ложно выставлять `tradingview_verified=true` для manual fixtures.

### 15.4. Независимый final review

Reviewer проверяет не только код и зелёные totals, но и scope denominator, все исходные blockers, происхождение expected, применимость version rules, настоящее выполнение paths, source graph и package content. Обязательно повторяет representative positive smoke и перечисленные tamper tests на итоговых artifacts.

Review не должен быть простой подписью автора к собственному summary. Любой новый подтверждённый обязательный дефект возвращает candidate в `in_progress` и требует исправления/повторной приёмки.

---

<a id="s16"></a>
## 16. Обязательный итоговый отчёт и передача результата

`FINAL_STAGE2_REPORT.md` должен содержать следующие данные, без расплывчатого «всё работает»:

| Раздел | Что включить |
|---|---|
| Summary | Что закрыто в 2.1–2.10, какие исходные OP затронуты, итог четырёх критериев. |
| Source reconciliation | Исходный archive hash, сравнение с Git/experiments, выбранные revisions/tree hashes восьми компонентов. |
| Architecture | Как сохранён origin/version context, где owners, какие schemas/ABI/public contracts изменились. |
| Historical authority | Решение CAT-04, источники, applicability, conflict review; полнота всего каталога, не только одного примера. |
| Oracle | Решение STATE-03/04, manual/TV provenance, denominator, assignments, version/type/edge coverage, все group/path receipts. |
| Language/imports | Allowed/rejected matrix, types/calls/state/lifecycle/restore, original diagnostics, таблица 2.1–2.10. |
| Findings | Все S2-01–25 и CAT/STATE/IMPORT/BUILTIN-01–04 с code/tests/evidence. |
| Test results | По каждой Python/component/suite: executed, passed, failed, errors, skips, inventory hash, log/JUnit paths. Поднаборы не суммировать как уникальные тесты. |
| Workers/UI/packages | Реальные команды, environments, resource/clean-install проверки и результаты. |
| Failures encountered | Исходные/промежуточные/повторные failures, причины, fixes и linkage attempts; не удалять неудачные логи. |
| Commits | Repository, commit/tree, scope, integration/publication references и remote readback при публикации. |
| Compatibility | Изменение artifact/cache/checkpoint/library lock; supported migration либо точный отказ старого формата. |
| Performance | Измерения только если выполнены. Иначе: «Производительность на этом этапе не измерялась». |
| Final delivery | Состав ZIP, SHA-256, verifier command, round-trip receipt, единственный current acceptance path. |
| Remaining | Для принятого этапа — отсутствие обязательного остатка; при blocker точный факт и отсутствие Accepted. |

Пользователю передаётся один основной полный ZIP, а не только отчёт/patches и не ссылка на незаписанный файл. В сопровождающем сообщении привести итоговый статус, ключевые результаты обеих Python, workers/frontend/packages, ссылку на архив и отчёт. Не заявлять исполнения, которое не представлено реальными outputs.

---

<a id="appendix-a"></a>
## Приложение A. Все 25 замечаний аудита: обязательная повторная приёмка

**Статус на входе ниже взят из R1, не из нового прогона.** «Исправлено» означает устранение прежнего контрпримера, не полную приёмку подсистемы. Все 25 ID должны присутствовать в final findings registry с конкретными code/test/evidence references.

| ID | Состояние по R1 | Что требуется подтвердить в финальной поставке | Связь |
|---|---|---|---|
| S2-01 | Исправлено | Folding, inputs/active и runtime используют согласованный versioned `%`; все знаки, int/float, NA, нулевой делитель и mixed-library origin. Отдельный input workaround не допускается. | F1, F2, F4.2–3. |
| S2-02 | Исправлено | Bool и числа не смешиваются через Python equality/truthiness; допустимые сравнения согласованы на folding/runtime, недопустимые отвергаются по подтверждённому typing. Проверить `false == 0` и `true != 1` как regression/admission cases. | F1, F2, F4.3. |
| S2-03 | Исправлено | Version-applicable exact integer division не проходит через binary64; `9007199254740993 / 1` в исходном v5 const-контексте не теряет единицу. Проверить ±(2^53±1), signed boundaries, отрицательные operands, compiled и defaults. | F2, F3, F4.2–3. |
| S2-04 | Исправлено | Прямые literal-NA comparisons запрещаются по version contract; разрешённые typed NA variables/`na(x)` не блокируются тем же правилом. | F1, F4.3. |
| S2-05 | Исправлено | UDT field defaults проверяются по форме и типу: запрещённые expression/call/global default отвергаются; allowed literal/builtin/enum cases не сломаны; local/imported declarations. | F2, F4.6. |
| S2-06 | Исправлено | `nz` и `color.r` сквозные positive paths с точным ABI/overload и независимым expected; проверить дополнительные g/b/t, omitted vs explicit NA replacement и invalid transport values. Старые expected-failure tests не считаются реализацией. | F1, F3. |
| S2-07 | Исправлено | `str.contains`, substring/replace/replace_all и связанные string signatures правильны по версии, именам, порядку, defaults/overloads; generator и runtime согласованы. | F1, F3. |
| S2-08 | Исправлено | `str.startswith`/endswith/split и все repaired forms передают конкретные `actual_type`/`expected_type`; consumer invariants не ослаблены. | F1, F3. |
| S2-09 | Исправлено | `array.from`/`array.sum`: variadic/generic binding, element types/promotion, empty/NA, slices и matrix row; exact target mapping и независимые результаты. | F1, F3, F4.5. |
| S2-10 | Исправлено | `str.tostring(enum)` возвращает title по nominal registry; same-name local/imported enum, unknown/wrong member/type и неподдерживаемый format не смешиваются. | F2, F3, F4.6. |
| S2-11 | Исправлен gate; полнота не принята | Catalog inventory integrity не выдаётся за contract/authority completeness. F1 закрывает реальные обязательные `UNVERIFIED`, а gate обнаруживает новые. | F1, F5. |
| S2-12 | Исправлен gate | Required dimensions сверяются с независимым набором; мутации удаления parameter/default/return/qualifier/overload вызывают отказ. Проверка не тавтологическая. | F1.6, F5.6. |
| S2-13 | Открыто | Весь required historical/version-exact contract подтверждён; CAT-04 разрешён; нет extrapolation современного API назад; corpus provenance проверяем. | F1 целиком. |
| S2-14 | Открыто | Полная allowed/rejected version matrix и origin-preserving imports; нет ограниченного stateless proof вместо реализации, нет переписывания source version. | F2 целиком. |
| S2-15 | Открыто | Полный независимый oracle и пять путей всех обязательных groups/variants; STATE-03/04 разрешены; exact assignments, warmup/NA/types/tolerance. | F3 целиком. |
| S2-16 | Исправлено | Callable/catalog/ABI locks соответствуют реально пересмотренным contracts; исходные 2390 rows прослеживаются; нет blind rebaseline. | F1, F5. |
| S2-17 | Исправлено | Canonical self-hash remaining-matrix и matrix-lock reference действительны; tamper обнаруживается; команда выдаёт реальный verdict, не hash exception. | F5. |
| S2-18 | Исправлено | Publication JSON присутствуют в реальных wheel и sdist, доступны после clean install; новые обязательные import/semantic resources также упакованы. | F6.4–5. |
| S2-19 | Исправлено | Partial/local verification не называется полной coordinated publication; все наблюдения обязательны в coordinated mode. | F5.5–6. |
| S2-20 | Исправлено | Full=true невозможен при незакрытых критериях/residuals, missing/stale receipts или другом source lock; проверить реальное содержимое evidence, не только флаги. | F5.5–6. |
| S2-21 | Исправлено | Legacy identifier policy точная: запрещённый идентификатор ловится, новое имя не даёт ложный substring FAIL; защита не удалена; полный Pine2AST suite green. | F4, F6. |
| S2-22 | Исправлено | Current verdict один; historical matrix явно отделена; progress/remaining/publication/manifest/README согласованы с финальной кампанией. | F5, F7. |
| S2-23 | Открыто | Один exact eight-component candidate, обе Python, обязательные inventories, worker/sandbox, Stage 1, architecture, frontend, real packages и round-trip. | F0, F5–7. |
| S2-24 | Исправлен тест | Once впервые выполняется на unconfirmed tick; есть настоящие rollback/replay/commit/checkpoint/new-runtime restore и fill-recalculation проверки. Нет disabled checkpoint. | F4.9. |
| S2-25 | Исправлен тест и parser regression | Тест возвращает tuple из loop, а не только присваивает два scalar; empty/break/continue/nominal/reference combinations, соседние DEDENT blocks; scalar scenario сохранён. | F4.8. |

Для каждого ID final registry должен хранить `requirement`, `root_cause`, `code_refs`, `tests`, `independent_basis` где нужен, `run_receipts`, `final_status`. `closed` без ссылки на реально исполненный тест/проверку не проходит final gate. У четырёх открытых ID закрытие по одному частному примеру недопустимо.

---

<a id="appendix-b"></a>
## Приложение B. Все 16 пунктов `stage2-remaining-matrix.json`

Ниже приведены **записанные во входном файле labels**, включая исторические/ограниченные `accepted_*`. Они не отменяют `not_accepted` в current receipt и не удостоверяют окончательную поставку. 16 work packages — не знаменатель всех Pine semantic cases и не процент завершённости.

| ID | Входной label | Полный критерий окончательного закрытия | Пакет |
|---|---|---|---|
| CAT-01 | `accepted_installed_denominator` | Сопоставлен полный version-applicable внешний required catalog, а не только installed surface; все обязательные сигнатуры/negative pairs представлены. | F1.1–3. |
| CAT-02 | `accepted_explicit_unverified` | Для каждой unavailable/unverified/delegated строки установлен настоящий контракт, owner и stage scope; mandatory gaps устранены, строки не удалены. | F1.2/6, §3.3. |
| CAT-03 | `accepted_admitted_profile` | Полная declared qualifier/type/default/overload область согласована producer→consumer→target и проверена, не только удачный const profile. | F1.5, F4.2. |
| CAT-04 | `unresolved_authority` | Независимо разрешены v4 availability и absent-neighbor behavior для binary_search family; catalogue/runtime/expected/negative tests приведены в соответствие. | F1.4, F3. |
| STATE-01 | `accepted` | Current version×type×callsite×lifecycle matrix покрывает всю обязательную область и исполнена на финальном candidate. | F4. |
| STATE-02 | `accepted_admitted_combinations` | Все требуемые generic/reference/overload combinations, nominal/field rules и ошибки проверены; admitted subset не выдаётся за complete. | F2, F4.5–7. |
| STATE-03 | `residual_unresolved_authority` | Независимые RSI/MACD v1/v2 seeds/warmup/NA и пять paths; поздняя версия не используется как единственное основание ранней. | F3.6. |
| STATE-04 | `residual_unresolved_authority` | TSI startup/flat/NA правила установлены и проверены на всех путях, включая первые бары и restore. | F3.7. |
| IMPORT-01 | `accepted` | Source-bound namespace method calls сохраняют explicit receiver, overload, visibility, qualifiers и state; mixed contexts не ломают receiver-dot. | F2, F4.7. |
| IMPORT-02 | `accepted_same_version_only` | Реализованы все подтверждённые allowed consumer/library пары с origin policy и exact dependency identity; запрещённые имеют negative coverage. | F2.1–10. |
| IMPORT-03 | `accepted_admitted_profile` | Полные required transitive/public/private/type/method/overload contexts по точной декларации; нет leakage private или доступа через обходной импорт. | F2.4/5/7. |
| IMPORT-04 | `accepted` | Обычные API/CLI допускают exact library lock, сохраняют оригинальные diagnostics, clean package path и no latest/sibling fallback. | F2.7, F6.5. |
| BUILTIN-01 | `partial` | Все required direct rows имеют независимые cases/assignments и пять исполненных путей; NO_EXAMPLES/PARTIAL устранены без shrink. | F3.1–5/8. |
| BUILTIN-02 | `accepted_documented_numeric_profile` | Для каждого overload закрыты NA/warmup/length/ties/tolerance/state/abort, а не несколько examples all paths. | F3.5–9. |
| BUILTIN-03 | `accepted_attributed_subset` | Все требуемые ранее неатрибутированные array/string и новые cases связаны с exact contracts; нет orphan/deleted assignment для улучшения отчёта. | F3.2/4. |
| BUILTIN-04 | `accepted_on_python312` | Полная новая Python 3.11/3.13 кампания со всеми owners/workers/corpora/frontend/packages; старые Python 3.12 receipts не переносятся. | F5–7. |

Current remaining verifier обязан проверять содержательную полноту этих пунктов и реальные receipts. Поиск префикса `accepted` в строковом label не является правилом завершения.

---

<a id="appendix-c"></a>
## Приложение C. Существующие точки изменения в исходниках

**Обозначения путей:** `D` — `openpine/` от корня распакованного ZIP; `S` — `D/sources/`; `H` — `S/openpine/`, корень host repository. Без иного префикса `verification/...` в этом документе относится к `H/verification/...`. Полный путь вида `pine2ast/pine2ast/...` ниже указан относительно `S`.

Это навигация по входному дереву, не требование сохранять плохую архитектуру. После reconciliation имена/расположение могут измениться с documented migration и обновлением всех consumers.

| Область | Существующие файлы/каталоги для чтения и изменений |
|---|---|
| Каталог и генерация | `pine2ast/pine2ast/reference_catalog/`; `pine2ast/pine2ast/hardening/historical_catalog.py`; `pine2ast/pine2ast/hardening/historical.py`; producer reference/generator entrypoints, найденные в этом компоненте. |
| Folding / typing | `pine2ast/pine2ast/semantic/binder.py`; `pine2ast/pine2ast/semantic/analyzer_statements.py`; смежные semantic owners. |
| Library store/linker | `pine2ast/pine2ast/libraries/store.py`; `linker.py`; `method_projection.py`; `qualifier_context.py`. |
| Producer consumer contract | `pine2ast/pine2ast/hardening/consumer_bundle.py`; `invariants.py`; schemas в `hardening/schemas/`. |
| Producer publication | `pine2ast/pine2ast/hardening/language_publication.py`; `stage2_10_language_publication.json`. |
| Compiler boundaries | `ast2python/ast2python/admission/`; `compiler.py`; `artifacts/`; `target_data/`. |
| Compiler IR / lowering | `ast2python/ast2python/lowering/model.py`; `builder.py`; `validate.py`; `recipes.py`; `qualifiers.py`; `pinelib_target.py`; `binding_audit.py`. |
| Compiler emission | `ast2python/ast2python/emission/language.py`; `python.py`; `loops.py`; `nominal.py`; `metadata.py`; затронутые request boundaries. |
| Runtime policies / execution | `pinelib/pinelib/runtime/context.py`; `policies.py`; `language.py`; `session.py`; `pinelib/pinelib/core/values.py`. |
| State / references / TA | `pinelib/pinelib/state/`; `reference/`; `ta/`; `builtins/`. |
| ABI / manifest | `pinelib/pinelib/abi/`; в частности `models.py`, `primitives.py`, `reference.py`, `manifest_v2_builder.py`. |
| Host compile / diagnostics | `openpine/openpine/compile/native_rc6.py`; `pipeline.py`; `library_inputs.py`; `source_context.py`; `diagnostics.py`; public CLI/API callers. |
| Host artifacts и worker | `openpine/openpine/artifacts/store.py`; `openpine/openpine/runtime/`; integration `rc6_tests/`. |
| Catalog/remaining/index gates | `openpine/openpine/verification/stage2_catalog.py`; `stage2_remaining.py`; `evidence_index.py`; `builtins.py`; `stage_gate.py`; `identity.py`; `pytest_gate.py`. |
| Канонические документы | `H/verification/stage2-current-acceptance.json`, source/callable/evidence-plan locks, progress/remaining/publication и version matrices. |
| Oracle inputs | `H/verification/builtins-v1/`; все `builtin-*-v1/` из F3; `builtin-assignment-locks/`; derivation scripts и их reviews. |
| CI | `H/.github/workflows/rc6-native.yml`; `H/docs/RC6_LIFECYCLE_SOURCES.json`; `H/rc6_tests/selected_regressions.json`; `H/verification/inventory.json`. |
| Frontend | `H/openpine-ui/package.json`; `package-lock.json`; actual tests/source/build config. |
| Packaging | `pyproject.toml`, `MANIFEST.in` где используются и package data каждого компонента; public distribution resource loaders. |
| Root delivery | `D/verify_sources.py`; `D/run_repair_checks.py`; `D/DELIVERY_MANIFEST.json`; source/receipt links внутри них. |
| Экспериментальное продолжение | Отдельные `continuation-work/run_exact_stage2.py`, `build_stage2_package.py`, workflow template и три library-related файла. Не импортировать их молча в production. |

Сохранить/расширить существующие regression tests, особенно `pine2ast/tests/stage2/test_stage2_import_version_matrix.py`, `pine2ast/tests/stage2/test_distribution_boundary.py`, `ast2python/tests/test_locked_library_execution.py`, `ast2python/tests/test_stage27_control_execution.py`, `ast2python/tests/test_stage29_builtin_expected.py` и соответствующие host verification tests. Поиск consumers и точных test IDs выполнить на выбранной базе; эта таблица не заменяет полный inventory.

---

<a id="appendix-d"></a>
## Приложение D. Минимальные машинные контракты и сценарии подмены

### D.1. Requirements registry

Для всех F-пакетов, подэтапов, 25 findings и 16 remaining items должна быть машинная трассировка. Можно расширить существующие artifacts; не создавать второй независимый каталог требований.

Минимальная запись содержит:

```text
requirement_id
normative_source + section
substage + main_criterion
owner/component
applicability: versions/types/call contexts/lifecycle/paths
contract_or_authority_refs
implementation_refs
positive_test_ids
negative_test_ids
independent_fixture_refs, where required
execution_receipt_refs
status + unresolved_reason
```

Обязательный requirement без applicability или evidence links не закрывается. `not_applicable` требует конкретного language/scope основания и negative test, когда речь о недоступной форме языка. Отсутствующий implementation не является основанием `not_applicable`.

### D.2. Semantic origin record

Проектный контракт для F2; имена полей можно сопоставить с уже имеющейся схемой:

```text
module_identity:
    exact_library_ref_or_root_source
    original_source_checksum
    declared_pine_version
    direct_dependency_refs
    transitive_dependency_identity

semantic_origin:
    module_identity
    source_declaration_identity
    lexical_expression_or_callsite_identity
    admitted_catalog_identity
    resolved_contract_or_overload_identity
    semantic_policy_identity
    nominal_registry_identity, where applicable
```

Не требовать дублировать этот объект на каждом узле, если immutable interned table/reference обеспечивает те же гарантии. Проверить отсутствие dangling origin references и невозможность подменить версию одной строки после admission.

Boundary typing и runtime execution используют проверенный record, а не пользовательскую неподписанную аннотацию. Changes участвуют в artifact compatibility; caches не принимают старый artifact под новой policy.

### D.3. Oracle contract record

Для каждой обязательной формы:

```text
contract_id = version + symbol + overload + call-form
availability_and_signature_authority
admitted_argument_domain
return_contract
state_and_na_contract
required_behavior_dimensions
independent_fixture_ids
assignment_identity
mandatory_execution_paths
coverage_status
```

Required dimensions должны задаваться независимо от того, какие examples уже существуют. Нельзя вычислять completeness как «все поля, которые мы нашли в examples, представлены в examples».

У `manual_fixture` зафиксировать derivation; у `tradingview_export` — external source/data/settings/version provenance. `missing` сохраняет explicit blocker. Metamorphic tests могут ссылаться на contract, но не выполняют обязательство independent expected вместо fixture.

### D.4. Пример непринятого состояния

Ниже **схематический пример**, а не готовый receipt и не новый API. Реальная схема должна быть версионирована и проверяться production verifier; placeholder hashes не допускаются.

```json
{
  "status": "in_progress",
  "full_stage2_accepted": false,
  "criteria": {
    "versioned_catalog": "not_accepted",
    "stateful_language_matrix": "not_accepted",
    "imports": "not_accepted",
    "independent_builtin_expected": "not_accepted"
  },
  "source_lock_hash": null,
  "execution_receipts": [],
  "open_findings": ["S2-13", "S2-14", "S2-15", "S2-23"]
}
```

В реальном принятом receipt `source_lock_hash` и все обязательные references непусты, существуют и проверены. Недостаточно заменить `false` на `true`, очистить `open_findings` и проставить четыре строки `accepted`.

### D.5. Дополнительные сценарии отказа, специфичные к этим остаткам

| Сценарий | Требуемая защита |
|---|---|
| Consumer v6 получает library v5, но lowered expression потерял origin и использовал root policy. | Semantic-version fixture расходится; type/artifact validation обнаруживает потерянный origin. |
| В checksum-валидный checkpoint подставлен другой exact library graph без соответствующего compatibility update. | Restore отвергается до продолжения программы. |
| Для CAT-04 поставлен status verified со ссылкой только на общую современную страницу. | Authority applicability/claim completeness gate не принимает historical contract. |
| RSI expected получен вызовом самой тестируемой runtime-функции через «reference wrapper». | Independence review/check обнаруживает зависимость; fixture не закрывает oracle criterion. |
| Из plan убран `compiled_checkpoint`, а остальные paths green. | Plan lock/no-shrink не даёт accepted. |
| Один успешный scalar `nz` fixture назначен нескольким неподходящим overloads. | Exact assignment/type/domain coverage не проходит. |
| Прогон Python 3.13 переименован в каталог `python-3.11`. | Interpreter receipt/observations mismatch вызывает отказ. |
| Установка wheel не содержит JSON, но loader нашёл его в соседнем source tree. | Clean installed-package path и resource identity checks обнаруживают обход. |
| Новая итоговая документация помечена Accepted, current receipt false. | Consistency gate и archive verifier отклоняют поставку. |

---

<a id="appendix-e"></a>
## Приложение E. Исходные identities и источники

### E.1. Проверяемые входные файлы

| Источник | SHA-256 оригинальных байтов |
|---|---|
| `OpenPine_Stage2_Continuation_2026-09-18(1).zip` | `d96ccc7453b56da5fa0b0888c04144af0eb5255e5dd5d9078a511b5798516581` |
| N1: `ТЗ(2).txt` | `85db475c22ff5b422c6a185739a1d731e5176a17cd76c801ea08f86280107ff3` |
| N2: `этап 2.txt` | `96aa0c3bc7813fab9c89f2dd68bfd3c14c1231dd46b31a3d4cb3443fb7fdeca3` |
| A1: `AUDIT_OpenPine_Stage2_2026-09-18.md` | `5b9331e9adc8d749777c1dad9e69e927c810feaf9a77d604304908f663774f13` |
| R1: `openpine/REPAIR_REPORT.md` внутри ZIP | `2cc9ff8642d3d42946546c9df010c48b40a6dd53fccea659b82ed4f3c844df32` |
| R2: `CONTINUATION_STATUS.md` внутри ZIP | `7bbfd0e02c321cc20bbcf41b71faf474135e726ea27aff8c18ba1d21294b0f90` |

В архиве копия Master ТЗ также находится в `openpine/delivery-evidence/spec/MASTER_TZ.txt`; её байты соответствуют N1. Сохранить исходные нормативные файлы рядом с final evidence либо включить их в документированный source bundle.

### E.2. Baseline canonical identities

Следующие значения — **поля входных artifacts**, а не hashes будущей реализации:

```text
stage2-source-lock.json / content_hash:
sha256:1b2fa36bff884fa92c853efadf722b74dd3be637e10d0b2b2021b256be4ea8b1

stage2-current-acceptance.json / content_hash:
sha256:2c4f1c49eac35cae3b026b324995513260eeb191c2137c170a6d1d0c6f8c7488

stage2-evidence-plan.json / content_hash:
sha256:b5bcf397cf49d2f0702b59e0218b8861c88134e71e5f756ae3d0134feceb943c

stage2-callable-lock.json / content_hash:
sha256:abf209bd02b7eefab60e547ce32c20a513286fdad30222c5f2a171be95a0a8a9
```

Эти canonical hashes не равны автоматически raw SHA-256 JSON-файлов: использовать алгоритм существующей схемы. Изменение тела требует соответствующего canonical recalculation и review, но recalculation не подтверждает корректность содержимого.

### E.3. Откуда взяты исходные выводы

| Вывод | Файл/раздел источника |
|---|---|
| Четыре основных DoD и дополнительные gates | N1 §7.13; N2 2.10. |
| Полный состав language/UDT/enum/method/loop/once/import/oracle | N1 §7.2–7.12; N2 2.1–2.9. |
| Запрет shrink/self-oracle/sandbox bypass | N1 §3, §6.2, §15–16. |
| Четыре открытых finding и 21 прежнее исправление | R1, таблица статусов; `stage2-current-acceptance.json`. |
| Основная поставка не интегрирована с X1 | R2; побайтовое сравнение ZIP с repaired candidate. |
| Ошибки экспериментального import harness / disabled integration | `continuation-work/completion-evidence/mixed-version-runtime.json`, `mixed-version-integration.json`. |
| Реальное ограничение main linker | `S/pine2ast/pine2ast/libraries/linker.py`, `_Linker` version admission. |
| Catalog counts / authority incompleteness | `D/repair-evidence/results/catalog.json`; это сохранённый отчёт, не новый run. |
| Callable count и отсутствие новых oracle run receipts | `D/repair-evidence/results/builtin-index.json`. |
| Двенадцать групп и сто variant/path obligations | `H/verification/stage2-evidence-plan.json`. |
| Исторические ограниченные remaining labels | `H/verification/stage2-remaining-matrix.json`, items. |
| Existing CI и frontend/build sequence | `H/.github/workflows/rc6-native.yml`; component pyproject metadata. |

Подготовка ТЗ включала чтение предоставленных требований, отчётов и выбранных исходников/артефактов, а также проверку состава и hashes архива. Это не новый полный аудит кода и не подтверждение всех старых `fixed` статусов. Внешняя Pine-документация при составлении этого документа заново не проверялась; её целевая независимая проверка предписана исполнителю в F1–F3.

---

<a id="appendix-f"></a>
## Приложение F. Готовая инструкция для запуска работы исполнителем

Следующий текст можно передать исполнителю вместе с этим MD и исходным ZIP:

> Заверши OpenPine RC6 Stage 2 полностью по `OpenPine_Stage2_Final_Completion_Spec_2026-09-18.md`. База — приложенный `OpenPine_Stage2_Continuation_2026-09-18(1).zip` с SHA-256 `d96ccc7453b56da5fa0b0888c04144af0eb5255e5dd5d9078a511b5798516581`. В нём основное `openpine/` — прежняя repaired-поставка, а `continuation-work/` — непринятые эксперименты; их нельзя считать уже интегрированными или успешно проверенными.
>
> Выполни F0–F7 как одну задачу: восстанови exact baseline и окружение; закрой полный version-exact каталог с historical authority, включая CAT-04; реализуй все подтверждённые allowed mixed-version library combinations с сохранением semantic origin; закрой полный независимый builtin oracle, включая STATE-03/04 и весь locked execution plan; сохрани и повторно проверь все 25 замечаний и полную 2.1–2.10 матрицу.
>
> Не ограничивайся одним примером деления, stateless-подмножеством библиотек, созданием runner/YAML или поправкой статусов. Не генерируй expected самим OpenPine. Не сокращай inventory/plan, не выключай sandbox, не подменяй protected worker, не ослабляй consumer/ABI и не создавай второй semantic owner.
>
> Сначала подготовь реальные Python 3.11/3.13, dependencies, frontend toolchain и защищённый worker host. Затем собери согласованный candidate восьми компонентов и выполни полный CI на одном source lock: language/component inventories, all builtin paths, imports/state lifecycle, Stage 1, architecture, protected workers, frontend, реальные wheel+sdist и clean installed-package checks. Исторические PASS и эксперименты не являются receipts нового дерева.
>
> Внутри задачи работай независимыми пакетами и небольшими коммитами с Lead и максимум четырьмя одновременно активными specialist agents, если такой режим доступен. Итог — один полный интегрированный ZIP с исходниками, builds, locks, журналами, независимыми expected, current acceptance и подробным отчётом. Проверь фактическую распаковку и SHA-256 готового архива.
>
> `full_stage2_accepted=true` устанавливай только после реального выполнения всех требований раздела 15. При настоящем внешнем blocker укажи точные выполненные попытки и незакрытый критерий; не объявляй этап завершённым и не скрывай отсутствие проверки. Но не останавливай доступную реализацию после очередного частичного пакета и не заменяй требуемую работу новым планом.

---

**Итоговое требование:** закрыть не четыре формальных номера, а всю обязательную поверхность этапа 2 с одним согласованным исполненным доказательством. Результат должен быть пригоден для продолжения RC6 с этапа 3 без открытого обязательного долга этапа 2.
