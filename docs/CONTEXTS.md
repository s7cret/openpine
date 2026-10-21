# Контексты и исторические запросы R6

Реализованы шесть частей: контракт рынка, несколько снимков в RunPlan,
порядок событий, запросы close/EMA, совместный checkpoint и сквозная приёмка.
Все контексты задания исполняются последовательно в одном worker.

## Версионный срез

Профили `openpine.requests.v1.1` … `v6.1` закреплены новым выпуском
`pine-requests-20261021.1` с evidence из локальных версионных каталогов wiki.
Прежние bundles не изменены. Это ограниченная модель по артефактам;
сравнение с TradingView остаётся отдельной приёмкой.

| Pine | API | Флаги | Default lookahead |
| --- | --- | --- | --- |
| v1–v2 | `security(x,y,z,a)` | Positional bool gaps; named не подтверждены | `on`, отдельного параметра нет |
| v3–v4 | `security(symbol,resolution,expression,gaps,lookahead)` | bool либо `barmerge.*` | `off` |
| v5–v6 | `request.security(symbol,timeframe,expression,gaps,lookahead)` | Константы `barmerge.*` | `off` |

Default gaps — off. Выражение — `close`, `ema(close,3)` либо `ta.ema(close,3)`
с literal int length. EMA сохраняет версионные seed/NA-правила. Два написанных
запроса получают независимое состояние, обновляемое на requested bars.

Исключены динамические/вложенные запросы, запросы в пользовательских функциях
и их аргументах, условные ветки,
short-circuit/var-initializer с запросом, произвольные captures/выражения,
tuples, lower-TF, currency, `ignore_invalid_symbol`, `calc_bars_count`.
Конфликтующий default последнего для v5 не выбирается. За пределами среза
выдаётся явная диагностика. Полного покрытия security пока нет.

## Контракт рынка и времени

`MarketContext`: `context_id`, `symbol`, `timeframe`, `source`, `timezone`,
`session`, `time_rule`; в JSON обязательны все поля. Source сверяется с
provider в provenance снимка, остальные идентификаторы — с dataset identity.
Неоднозначные провайдеры одного symbol/timeframe внутри плана отклоняются.

Первый профиль: канонические минуты `"1"` … `"1440"`, `timezone="UTC"`,
`session="24x7"`, `time_rule="fixed-duration-utc.1"`. Время означает открытие
бара в UTC milliseconds. Потреблённые бары лежат на сетке UTC epoch.
Requested timeframe не меньше основного и кратен ему. Другие зоны, календари,
месяцы и неполные сессионные бары не угадываются. Ресемплинга и создания
отсутствующих свечей нет.

Очередь объединяет события всех контекстов по времени доступности, затем по
стабильному ID контекста/режима. Это действует и при прогреве/скачках основного
графика. При совпадении времени requested event исполняется перед primary.

- Off: requested close time не позже primary close time. Часовая свеча 00:00
  появляется на 5m-баре 00:55.
- On: финальное историческое значение доступно с requested open time; та же
  свеча появляется на 5m-баре 00:00. Намеренное чтение будущего отмечается
  `historical_future=true`.
- Gaps off: последнее доступное значение сохраняется, включая настоящее na.
- Gaps on: значение выдаётся при новом событии доступности после предыдущего
  primary observation, иначе na. Перенос события через пропуск primary —
  явное решение нашей модели до внешней conformance.

Поведение старых версий подтверждено
[официальной документацией security v3](https://www.tradingview.com/pine-script-docs/v3/essential/context-switching-the-security-function/).
Контекстное вычисление и современные флаги описаны в
[документации запросов](https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/).
Внешнее исполнение наших fixtures не проводилось.

## План и восстановление

Новый `openpine.run-plan.3` содержит primary context и дополнительные
`ContextBinding(context,snapshot_id)`. Максимум — восемь контекстов вместе с
основным и 16 written requests. Один context/visibility-mode делит reader и
cursor; выражения имеют отдельные evaluations. Primary runtime не вычисляет
третий аргумент запроса на своих барах.

`openpine.context-state.1` сохраняет primary runtime, курсоры/состояния всех
requested groups, последние значения и хеш полного набора входов. Пауза
подтверждается после целого primary bar и всех его requested events.
Восстановление проверяет снимки, timestamp/cursor, программы выражений и их
доступность на сохранённом времени primary. Подмена входа, future off-cursor
или пропуск доступного requested bar отклоняются.

Корень `execution-checkpoint.1` сохраняет прежние ссылки; RunPlan связывает
его со всеми snapshot IDs. Каталог остаётся SQLite v3: новые данные размещены
в существующих immutable plan/request. Backup/restore/prune/GC сохраняют
дополнительные снимки. Границы Parquet-блоков выходов не меняются при паузе.

Новые форматы: summary .4 и малый полный run-result .3 с метаданными контекстов,
идентичностями дополнительных данных и режимами запросов. Одиночные задания
сохраняют result .2 / summary .3. Payload/хеш старых RunPlan .1/.2 сохраняются.
Изменение ядра/spec этого этапа требует перекомпиляции старого IR.

DuckDB-бюджет делится между primary reader, sink и максимум двумя readers на
контекст: сумма настроенных лимитов — не более половины worker memory.
Остаток нужен Python/runtime. Слишком маленький план получает явный отказ;
supervisor продолжает учитывать RSS всего процесса.

## CLI и HTTP

Готовые программы v1–v6 и данные: `examples/contexts/`.

```powershell
openpine job --source examples/contexts/v6.pine --bars examples/contexts/bars.json --contexts examples/contexts/inputs.json --state-dir .runtime/contexts --pause-after 17
openpine job --resume JOB_ID --state-dir .runtime/contexts
```

Файл `openpine.context-inputs.1` содержит `primary_context` и `contexts`.
Дополнительная запись: `context` и либо `bars` (относительно этого файла), либо
сохранённый `snapshot_id`. После импорта сохраняются только ссылки. Resume
не требует исходников/файлов контекстов; один state-dir имеет одного владельца.

HTTP сначала получает snapshot IDs через прежний `POST /api/snapshots`.
Новый `POST /api/jobs` с `schema_version="openpine.job-request.4"` принимает
`input_ref`, основной `snapshot_id`, `timeout_seconds`, `primary_context`,
`contexts` с context/snapshot_id. Дополнительно можно передать оба поля
`checkpoint_interval_bars` и `pause_after_bars` (null без паузы).
Transport .2/.3 и `/pause`, `/resume` сохраняются. Форму выбора контекстов в UI
этот этап не добавляет; существующий график выводит основные свечи и plot.

## Приёмка

38 новых conformance cases: 32 исполнения и шесть отказов, Pine v1–v6.
Проверяются 288 остановок на всех границах 48-барных программ, смешанные
on/off-курсоры одного снимка, равные времена, пропуски, NA, same-TF, отсутствие
повторного обновления EMA и влияния будущего на off-префикс.

Отдельные процессы проверяют все версии, падения worker/координатора,
максимальные восемь контекстов / 16 запросов, backup/restore/prune/GC,
HTTP v1–v6, равенство CLI/HTTP v1/v6 и установленный wheel вне checkout.

```powershell
python -m tools.release_gate
python -m tools.install_check
python -m tools.context_benchmark --directory ../context-proof --bars 1000000 --pause-after 123457
```

Большая приёмка сравнивает всю сводку и Parquet с непрерывным запуском после
паузы, backup/restore и продолжения новым процессом. Последние значения
рассчитываются независимо. Измерения:
[evidence/contexts-20261021.json](evidence/contexts-20261021.json).

Финальная локальная приёмка: 1000000 основных 5m-баров и 83334 часовых бара,
три миллиона выходных значений в 30 Parquet-блоках. На паузе primary cursor
123457, requested cursor 10288. Runtime JSON — 2274 байта; выходной CSV-хвост
и готовые блоки хранятся отдельно. Запрос задания — 672 байта, worker — 2010.
Непрерывный запуск — 32,234 с, до паузы — 6,645 с, продолжение — 27,952 с;
пиковый наблюдённый RSS worker — около 126 MiB на данном Windows-хосте.
Это измерение этого набора, а не общий SLA.
