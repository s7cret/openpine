> Локальный срез 2026-09-07: OP-31 частично реализован, но не опубликован и не принят целиком. 30 partial, 5 unverified, 1 accepted; это не процент совместимости.

# OpenPine RC6 — все 36 пунктов после entry-risk блока

Статусы означают полную приёмку исходных пунктов, не процент реализации или совместимости. Новый runtime проходит отдельную приёмку; наличие теста не означает наблюдённый CI-pass. Исходные ID и заголовки сохранены.

Источник ТЗ: `OpenPine_RC6_Review_TZ_2026-09-05.md`, SHA256 `4df7ba22428e859fb60feb68027c8ad63f150a21d594bc12660284d9507cc515`. База среза: `b770989098577440481fa9eb44b8217ccf970159`.

| Пункт | Статус | Реализовано | Остаток до приёмки |
|---|---|---|---|
| **OP-01**. Одна воспроизводимая поставка вместо расходящихся идентификаторов | Частично | Закреплён совместимый набор commit SHA; CI устанавливает чистые копии источников. | Материализовать весь wheel manifest, dependency closure и doctor; проверить установку без исходников. |
| **OP-02**. Довести пользовательские inputs до исполнения и оптимизации | Частично | Overrides поступают в runtime, CLI и trials; проверяются типы и hash применённых inputs. Исправлены scalar input v1-v4 и source-input qualifier; max_position_size принимает применённый input, включая ноль, без утечки между запусками. | Зафиксировать единый output digest для CLI/API/trial, включая одинаковые title и все допустимые нули. |
| **OP-03**. Единая конфигурация стратегии: нули, sizing и брокер | Частично | Устранены потеря margin=0 и дубли конвертеров; сохранены rounding. Допущенный mintick разрешается в обоих режимах и входит в effective config hash. | Полная provenance конфигурации и версия defaults declaration; общий immutable effective config. |
| **OP-04**. Сохранить finality, revision и валидность marketdata envelope | Частично | Канонические envelopes проверяются в обоих режимах; finality и numeric underflow не подменяются. | Полная матрица приёмки revision policies и исправленных/отозванных рядов во всех входных путях. |
| **OP-05**. Общий lifecycle для historical, realtime и fill-recalculation | Частично | Общий cursor и fill recalculation; история фиксируется один раз на бар. | Сквозной historical→realtime, несколько тиков и var/varip с реальным транспортом. |
| **OP-06**. Bulk-result без скрытой потери результатов | Частично | Полная tape и проверяемые result chunks; ошибки процесса и незавершённость не считаются успехом. | Явные full/metrics-only profiles с проверкой обещанных outputs и целостности больших результатов. |
| **OP-07**. Довести strategy bridge до полного покрываемого API | Частично | Сохранены семь торговых команд, 17 скаляров, all-entry/per-fill exits, смешанные цены v6, trailing/TP и метаданные выходов. Добавлены allow_entry_in и max_position_size через существующий RiskIntent; global/input параметры проверяются, scope/API строятся из общего engine registry. Позиционные exit и абсолютные повторные входы покрыты предыдущими этапами. | Остальные risk и indexed trade methods; извлечение условных/локальных risk declarations, multiple direction conformance, full historical signatures, FIFO/ANY, fixed-stop/trailing arbitration и отложенное вычисление/доставка alerts. |
| **OP-08**. Подключить request engine к настоящим snapshot-данным | Частично | Работают source-context выражения, nested HTF/LTF, arrays и chunked preloads; добавлена привязка к trial. | Автоподготовка источников из UI/CLI, UDF/локальные capture, live/revision, FX и полный каталог request.*. |
| **OP-09**. Честные instrument, timeframe и language identities | Частично | Интервалы час/минута/месяц и pointvalue передаются в runtime. Брокер и Pine используют допущенный mintick; конфликт отклоняется до запуска. | Явные crypto/stock/futures metadata, calendar/session и basecurrency без предположений. |
| **OP-10**. Настоящий checkpoint / resume вместо маркера состояния | Частично | Реальные Pine checkpoints, request caches и receipt-derived cursors; атомарный restore. Native broker/realtime snapshots теперь содержат проверяемый risk-state; правила до checkpoint сохраняются после resume. Старые снимки без risk-state не доказывают прежнюю политику. | Полное возобновление broker+IPC+worker без дублирования orders/visuals после падения. |
| **OP-11**. Быстрый content-sensitive state hash | Частично | Проверены content-sensitive full/semantic hashes и равенство после восстановления. | Дифференциальные мутации всех сегментов, включая references/requests/orders; стоимость хеширования на больших рядах. |
| **OP-12**. Включить RC6 и cross-library проверки в обязательный release gate | Частично | Постоянный CI включает native и все семь библиотек; provider network cases выделены отдельно. | Immutable wheel-release gate, coverage thresholds, обязательная матрица capabilities и установка пакетов без source paths. |
| **OP-13**. Pine v6 once: весь путь от lexer до rollback | Требует проверки | Отдельная реализация и приёмка once в этом этапе не выполнены. | Проверить актуальную официальную семантику; lexer→IR→runtime→rollback и независимый oracle. |
| **OP-14**. Полный version-exact реестр Pine v1–v6 | Частично | Добавлены request/NA/array bindings и проверки версий v1–v6 для поддержанного поднабора. Промежуточные namespaces выводятся только из активного каталога; simple entry-risk inputs, legacy input и source-series различаются в парных тестах v1-v6. | Полный version-exact каталог, overload denominator и отрицательные пары доступности. |
| **OP-15**. Замкнуть capability graph producer → compiler → runtime → host | Частично | Worker объявляет closed_bar и отвергает неподдержанный checkpoint_v1; host surface строится из registry. | Единый producer/compiler/runtime/host capability graph с ABI invalidation и проверяемыми statuses. |
| **OP-16**. Проверить и закрыть bool-history v6 без поломки старых версий | Требует проверки | Полная парная приёмка bool-history v5/v6 ещё не зафиксирована в этом реестре. | Проверить прямой ABI и generated path, первый бар, offsets, if/switch и bool fields; исправлять только воспроизведённые расхождения. |
| **OP-17**. Stateful semantics: callsite, функция, ветка, цикл и история | Частично | Есть транзакции состояния, callsite identity, rollback и проверки повторных callbacks. | Корпус независимых callsites в функциях/ветках/циклах с точными трассами до и после оптимизаций. |
| **OP-18**. Численное и функциональное покрытие builtins | Частично | Есть runtime/TA-наборы и точные проверки NA/типов для отдельных операций. | Для каждой supported overload определить численный oracle/tolerance и версии; отдельно считать TV coverage. |
| **OP-19**. Отдельный пакет актуальных возможностей Pine 2026 | Требует проверки | Пакет новых возможностей Pine 2026 не принят как единое целое. | Сверить официальные изменения и реализовать positive/negative сквозные сценарии без объявления усечённого поведения полным. |
| **OP-20**. Проверка broker parity по событиям, а не только итоговому PnL | Частично | Сохранены causal price scanner/Bar Magnifier, all-entry lifetime, per-fill relative/absolute цены и резервы, trailing и native resume. Entry-risk ограничивает фактическую позицию при отправке и исполнении, не strategy.order; запрещённый reverse становится полным market close. Учтены closing component, qty-step/minimum, одновременные pending orders, невозможность роста clipped order и сохранение risk state. | Остальные risk, FIFO/ANY, fixed-stop/trailing arbitration, полная матрица комиссий/margin/liquidation/realtime/seed и независимый event oracle. Native policy updates и legacy snapshot compatibility не равны полной Pine conformance. |
| **OP-21**. Строгий импорт и эффективное чтение offline datasets | Частично | Строгие CSV/Parquet, явные timestamp/volume policies и bounded batches. | Индексировать повторные intrabar reads, provenance policies, row-group filtering и repeat-import canonical identity. |
| **OP-22**. Расширение рынков и видов графиков без выдуманной точности | Частично | Существующий криптовалютный путь не выдаётся за полную поддержку иных рынков. | Корпус stock/futures/FX, vendor quality, календари и честная политика synthetic charts. |
| **OP-23**. Оптимизация runtime storage и горячего барового цикла | Частично | Убраны отдельные лишние сериализации и копии timestamp-массивов при alignment. | Замер времени/памяти на 1k/10k/100k/1M bars и оптимизация storage без изменения доступной истории. |
| **OP-24**. Кеш компиляции и безопасная специализация | Требует проверки | Cache/identity код существует; полная приёмка OP-24 этим этапом не выполнена. | Проверить cold/warm, ABI/library/flags invalidation, concurrent atomic writes и равенство hit/miss outputs. |
| **OP-25**. Потоковый bulk, видимый прогресс и надёжная отмена | Частично | Есть монотонный прогресс и bounded result/request frames; inputs сериализуются однократно. | Fault-проверки отмены при загрузке/расчёте/экспорте, backpressure и partial/resume UX. |
| **OP-26**. Строгий optimizer runner contract и независимость trials | Частично | Устранена потеря warmup, добавлены private copies, explicit SHA identities и trial-bound request snapshots. | Закрепить полный serial/parallel/seed/replay контракт всех runner-типов и внешнего изменяемого состояния. |
| **OP-27**. Полезная оптимизация: воспроизводимость победителя и устойчивость | Частично | Failed/partial/nonfinite trials исключены из ranking; нулевые метрики сохранены; проверен обычный replay чувствительного trial. | Воспроизведение победителя из отчёта/UI с digest; locked train/validation/warmup и независимый holdout. |
| **OP-28**. Исправить гонку загрузки и масштабирование parity UI | Частично | Защищены page/loadAll гонки; finite bounds, 300k-point sampling и top-N labels покрыты тестами. | Финальная приёмка больших реальных экранов; browser/visual UX отдельно от in-memory Vue lifecycle. |
| **OP-29**. Единый пользовательский путь: редактор → настройки → результат | Требует проверки | Пять обязательных пользовательских UI E2E ещё не приняты. | Editor→inputs→result, missing MTF, unsupported capability, cancel; стабильные source/data/config при повторе. |
| **OP-30**. Довести visuals, alerts и logs до продукта | Частично | Семантические visual/alert/log tapes предусмотрены в runtime. | Численный экспорт и renderer UI, rollback объектов и отсутствие дублей alerts после полного resume. |
| **OP-31**. Pine library imports и воспроизводимое разрешение зависимостей | Частично, локально | Локальный stage2-кандидат: pinned offline same-version scalar imports, транзитивные зависимости, isolated names, build identity и state/checkpoint регрессии. В GitHub не опубликовано. | Согласованная процессная приёмка и публикация; межверсионные импорты, exported UDT/reference/method/enum/const/overloads, request capture, UI/gateway интеграция и полный независимый oracle. |
| **OP-32**. Один сквозной conformance pipeline и first-divergence report | Частично | TV parity UI и диагностические отчёты существуют. | Единый versioned corpus с независимым expected и первым bar/phase/source divergence; отделить vendor/data mismatch. |
| **OP-33**. Fault tests и границы изоляции без ослабления sandbox | Частично | Protected worker regressions и полный optimizer process-containment suite входят в CI без fallback. | Полный fault matrix от загрузки до экспорта и доказательства освобождения ресурсов/запрета путей и сети. |
| **OP-34**. Документация, doctor и сборка без ловушек | Частично | Документированы точные source pins, migration limits и фактические CI receipts. | Чистая установка/doctor от wheel manifest без editable/PYTHONPATH и автоматической модификации пользовательских данных. |
| **OP-35**. Сохранить семь компонентов, убрать дублирование семантики в адаптерах | Частично | Удалены подтверждённые заглушки/legacy; общие владельцы config, lifecycle, request transport сохранены. | Architecture checks для всех семи библиотек и отсутствие повторной бизнес-семантики в адаптерах. |
| **OP-36**. Сохранение полезных изменений и консолидация веток OpenPine | Принят в указанном объёме | В OpenPine оставлены четыре линии; полезные изменения перенесены, исходные tips сохранены тегами. | Консолидация относится только к OpenPine; другие репозитории не объявлены очищенными. |

Машинный реестр: `RC6_REVIEW_36.json`. Пути в нём — указатели на реализацию/тесты/отчёты, а не автоматическая сертификация. Остатки не заменяют нормативный текст исходного ТЗ. Все глобальные статусы сохранены: 29 partial, 6 unverified, 1 accepted.

Новый блок: [entry-risk](RC6_ENTRY_RISK.md). Его CI и публикация фиксируются отдельным отчётом после проверки.


## Remaining Spec binding — 2026-10-06

The existing 36 OP records above retain their original source, statuses and evidence.
The additive `remaining_spec_binding` in `RC6_REVIEW_36.json` records all 68 remaining
requirements from [the complete source](OPENPINE_5_0_REMAINING_SPEC_2026-10-06.md),
SHA-256 `3f09ed3901f8ecf1262ffa983c6eaf52a51ceaf2087abe98db2093d45d9ca622`.
The approved support policy is ordinary CPython `>=3.13,<3.14`, with the GIL enabled;
older multi-minor claims in this source are superseded by that policy. Pine versions
1–6, domain scopes and all functional obligations remain required.

The records preserve source lines, ordered Д/И/К, primary owners, consumer slices,
OP links, existing Stage 2 links, implementation/test/basis seeds and historical
input identities. Exact full-scope inventories, execution identities and raw
receipts are pending. Seed paths and registry completeness do not establish product
acceptance. All 16 Stage 2 items and owners remain unchanged; full Stage 2 and full
release acceptance remain false. Historical accepted OP-36 retains its original scope.

| Requirement | Owner | Д/И/К | Status | OP mapping | Stage 2 items |
|---|---|---|---|---|---|
| INT-01 | `openpine` | И+К | partial | OP-01, OP-09, OP-35, OP-36 | — |
| INT-02 | `openpine.verification` | Д+К | partial | OP-12 | — |
| INT-03 | `openpine.verification` | И+К | partial | OP-03, OP-12, OP-15, OP-35 | — |
| INT-04 | `openpine.verification` | К+Д | partial | OP-33 | — |
| INT-05 | `openpine.verification` | К+Д | toqualify | §10 only | — |
| INT-06 | `openpine.verification` | Д+К | toqualify | §10 only | — |
| INT-07 | `openpine.verification` | Д+К | partial | §10 only | — |
| INT-08 | `openpine.verification` | Д+К | toimplement | OP-03, OP-12, OP-15 | — |
| LANG-01 | `pine2ast` | Д+К | partial | OP-14, OP-15, OP-19 | CAT-01, CAT-02, CAT-03 |
| LANG-02 | `pine2ast` | И+Д+К | partial | OP-02 | CAT-03 |
| LANG-03 | `pine2ast` | И+Д+К | partial | OP-14, OP-18, OP-19 | CAT-04 |
| LANG-04 | `pine2ast` | Д+К | partial | OP-14, OP-19 | CAT-01, CAT-02 |
| LANG-05 | `pine2ast` | И+Д+К | partial | OP-17, OP-24, OP-31 | IMPORT-01, IMPORT-02, IMPORT-03, IMPORT-04 |
| LANG-06 | `pinelib` | К+Д | partial | OP-16, OP-17, OP-24, OP-31 | IMPORT-02, STATE-01 |
| LANG-07 | `pinelib` | К+Д | partial | OP-05, OP-13, OP-16, OP-17 | STATE-01, STATE-02 |
| LANG-08 | `pinelib` | И+К | partial | OP-17 | STATE-01, STATE-02 |
| LANG-09 | `pinelib` | Д+К | partial | OP-16, OP-18, OP-32 | BUILTIN-01, BUILTIN-02, BUILTIN-03 |
| LANG-10 | `pinelib` | Д+К | blocked | OP-18, OP-32 | STATE-03, STATE-04 |
| LANG-11 | `openpine.verification` | К+Д | toqualify | OP-18, OP-32 | BUILTIN-01, BUILTIN-04 |
| LANG-12 | `openpine.verification` | И+Д+К | partial | OP-09, OP-14, OP-19, OP-31 | CAT-01, STATE-01, IMPORT-02, BUILTIN-04 |
| DATA-01 | `openpine` | Д+К | toimplement | OP-08 | — |
| DATA-02 | `pinelib` | Д+К | partial | OP-08, OP-15 | — |
| DATA-03 | `pinelib` | Д+К | partial | OP-08, OP-22 | — |
| DATA-04 | `marketdata-provider` | Д+К | partial | OP-04 | — |
| DATA-05 | `marketdata-provider` | Д+К | partial | OP-09, OP-22 | — |
| DATA-06 | `marketdata-provider` | И+Д+К | partial | OP-21 | — |
| DATA-07 | `marketdata-provider` | Д+К | toimplement | OP-22 | — |
| DATA-08 | `openpine` | К | toqualify | OP-04, OP-08, OP-21 | — |
| RUN-01 | `openpine` | Д+К | partial | OP-05, OP-07, OP-13, OP-17 | — |
| RUN-02 | `backtest_engine` | Д+И+К | toimplement | OP-10, OP-17 | — |
| RUN-03 | `openpine-contracts` | Д+К | toimplement | OP-03, OP-04, OP-07, OP-10, OP-17 | — |
| RUN-04 | `openpine` | Д+К | toimplement | OP-10, OP-17 | — |
| RUN-05 | `openpine` | Д+К | partial | OP-10, OP-13, OP-17, OP-30 | — |
| RUN-06 | `openpine` | Д+К | partial | OP-06, OP-25, OP-30 | — |
| RUN-07 | `openpine.workers` | Д+К | partial | OP-25, OP-33 | — |
| RUN-08 | `pinelib` | Д+К | toqualify | OP-11 | — |
| BROKER-01 | `backtest_engine` | Д+К | partial | OP-07, OP-15, OP-20 | — |
| BROKER-02 | `backtest_engine` | Д+К | toimplement | OP-05, OP-07, OP-20 | — |
| BROKER-03 | `backtest_engine` | Д+К | partial | OP-02, OP-07, OP-20 | — |
| BROKER-04 | `backtest_engine` | И+К | partial | OP-07, OP-20 | — |
| BROKER-05 | `backtest_engine` | Д+К | partial | OP-07, OP-20 | — |
| BROKER-06 | `backtest_engine` | Д+К | partial | OP-07, OP-20 | — |
| BROKER-07 | `backtest_engine` | Д+К | partial | OP-05, OP-07, OP-20 | — |
| BROKER-08 | `openpine.verification` | К | toqualify | OP-07, OP-20, OP-32 | — |
| PERF-01 | `openpine.verification` | К+Д | toqualify | OP-11, OP-23 | — |
| PERF-02 | `pinelib` | Д+К | toqualify | OP-11, OP-23 | — |
| PERF-03 | `openpine.compile` | Д+К | toqualify | OP-24 | — |
| PERF-04 | `openpine.verification` | К | toqualify | OP-23 | — |
| OPT-01 | `optimizer` | Д+К | partial | OP-03, OP-26 | — |
| OPT-02 | `optimizer` | Д+К | partial | OP-26, OP-33 | — |
| OPT-03 | `optimizer` | Д+К | partial | OP-27 | — |
| OPT-04 | `optimizer` | Д+К | partial | OP-27 | — |
| OPT-05 | `optimizer` | Д+К | toqualify | OP-27 | — |
| UI-01 | `openpine-ui` | Д+К | partial | OP-02, OP-25, OP-27, OP-29, OP-31 | — |
| UI-02 | `openpine-ui` | Д+К | partial | OP-02, OP-25, OP-29, OP-31 | — |
| UI-03 | `openpine-ui` | К+Д | toqualify | OP-28 | — |
| UI-04 | `pinelib` | Д+К | partial | OP-06, OP-30 | — |
| UI-05 | `openpine.notifications` | Д+К | partial | OP-30 | — |
| UI-06 | `openpine.verification` | Д+К | partial | OP-06, OP-30, OP-32 | — |
| UI-07 | `openpine-ui` | К+Д | toqualify | OP-29 | — |
| REL-01 | `openpine.verification` | И+Д+К | partial | OP-01 | — |
| REL-02 | `openpine.verification` | Д+К | partial | OP-01, OP-31, OP-34 | — |
| REL-03 | `openpine.distribution` | Д+К | partial | OP-01, OP-03, OP-09, OP-34 | — |
| REL-04 | `openpine.distribution` | Д+К | partial | OP-34 | — |
| REL-05 | `openpine` | Д+К | toqualify | OP-10, OP-34, OP-35 | — |
| REL-06 | `openpine.verification` | Д+К | toimplement | OP-01, OP-12, OP-35 | — |
| REL-07 | `openpine.distribution` | И+К | partial | OP-01, OP-34, OP-36 | — |
| REL-08 | `openpine.distribution` | К | toqualify | OP-01, OP-34, OP-36 | — |
