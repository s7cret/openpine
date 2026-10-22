# Параметры и воспроизводимые эксперименты

Одна компиляция Pine v1–v6 допускает несколько независимых наборов параметров.
IR, полный нормализованный набор значений и снимки данных имеют отдельные
идентичности. Задание сохраняет их до старта; изменение формы или пресета
не меняет уже созданный запуск. Требуется Python **3.14.8**.

## Версии и границы профиля

Источники — закреплённые локальные каталоги и новый неизменяемый выпуск
`spec/bundles/pine-inputs-20261022.1`. В его manifest указаны хеши записей
и явно обозначены реконструкции ранних версий. Это проверяемая модель OpenPine;
точное сравнение с TradingView остаётся отдельным этапом.

| Форма / возможность | v1–v2 | v3 | v4 | v5 | v6 |
|---|---|---|---|---|---|
| `input(defval, title)` | да | да | да | да | да |
| Явный legacy `type` | bare/literal | bare/literal | `input.integer` и аналоги | нет | нет |
| `input.int/float/bool/string/source()` | нет | нет | нет | да | да |
| `minval`, `maxval`, `step` | числа | числа | числа | typed int/float | typed int/float |
| Литеральные `options` | нет | да | да | да | да |
| `tooltip`, `group`, `inline` | нет | нет | да | да | да |
| Литеральный `active` | нет | нет | нет | нет | да |

В v4 `input.integer` — константа типа, а не функция. Поддержаны только
int64, конечный float, bool, string и выбор `open/high/low/close/volume`.
Числовые, логические и строковые параметры имеют qualifier `input`, источник —
`series float`. Типы проверяются строго: `true` не принимается как `1`.

Объявления должны быть обычными прямыми глобальными переменными. Метаданные
и defaults — литералы; source default — поддержанное имя OHLCV. Позиционно
разрешены `defval`, `title` и legacy `type`, остальные аргументы именованные.
До 64 параметров, до 64 вариантов в одном `options`. Options и числовой диапазон
не совмещаются. `step` задаёт положительное приращение для интерфейса,
не накладывает дополнительную сетку на исполнение.

Не входят: интерактивный `confirm=true`, динамический `active`, `display`,
внешние plot-источники, вложенные input-вызовы и другие input-типы.
`request.security` / legacy `security` по-прежнему требуют литеральные
symbol/timeframe и ранее поддержанное выражение. Параметризация графа запросов
не включена: неподдержанная зависимость диагностируется до исполнения.

## CLI

```sh
python -m apps.openpine compile --source examples/inputs/v6.pine --profile historical --output input-ir.json
python -m apps.openpine run --artifact input-ir.json --bars examples/inputs/bars.json --inputs examples/inputs/overrides.json
python -m apps.openpine job --artifact input-ir.json --bars examples/inputs/bars.json --inputs examples/inputs/overrides.json --state-dir .runtime/input-jobs
```

Аналогичные программы `v1.pine`…`v5.pine` включены в examples. Overrides — JSON
объект с именем переменной или стабильным ID `input:имя`. Неизвестные ключи,
дубли через два написания, неверные типы/границы/options отвергаются до worker.
Пропущенные значения заполняются defaults. Исходник повторно не компилируется.

Для браузера и других JSON-клиентов большое целое можно передать без потерь:
`{"length":{"$int":"9007199254740993"}}`. Допустимо только каноническое
десятичное представление int64. В сохранённых settings остаётся обычный int64;
типизированная транспортная оболочка не участвует в идентичности результата.
Скачивание IR выдаёт сохранённый артефакт напрямую: браузер не сериализует
его заново, поэтому большие целые и контрольная сумма сохраняются.

```sh
python -m apps.openpine experiment create --source examples/inputs/v6.pine --bars examples/inputs/bars.json --request examples/inputs/experiment.json --state-dir .runtime/input-jobs
python -m apps.openpine experiment run --id EXPERIMENT_ID --state-dir .runtime/input-jobs
python -m apps.openpine experiment status --id EXPERIMENT_ID --state-dir .runtime/input-jobs --start 0 --limit 25
python -m apps.openpine experiment list --state-dir .runtime/input-jobs
python -m apps.openpine experiment pause --id EXPERIMENT_ID --state-dir .runtime/input-jobs
python -m apps.openpine experiment cancel --id EXPERIMENT_ID --state-dir .runtime/input-jobs
```

`create` сохраняет состав без запуска. `run --request … --source … --bars …`
также создаёт и выполняет новую серию. Ctrl+C ставит серию на паузу.
`resume` сохраняет намерение продолжить; исполнение обслуживает работающий
веб-сервер либо `experiment run --id …`. Закрытие локального координатора
сохраняет активные задания эксперимента на паузе. Для общего с вебом каталога
учитывайте: `serve --state-dir DIR` использует `DIR/jobs`.

## Веб и HTTP

После компиляции появляется форма с defaults, именами, группами, подсказками
и выбором source/options. `active=false` показывает недоступное для ввода поле.
Именованный пресет привязан к хешу схемы параметров; при несовместимой схеме
он не предлагается. Обновление требует ожидаемую ревизию: устаревший клиент
не перезаписывает чужое изменение. До 100 пресетов на одну схему.

Панель «Сравнение параметров» принимает JSON-сетку `{"length":[2,3,4]}` либо
список `[{"length":2},{"length":4}]`. Остальные значения берутся из формы.
Каждый результат можно открыть на графике, включая после перезапуска приложения.

HTTP использует общий Host/Origin/token контроль и бюджет подготовки/чтения:

| Маршрут | Назначение |
|---|---|
| `POST /api/compile` | IR и `inputs` с метаданными схемы |
| `GET /api/artifacts/SHA256` | Сохранённый IR с проверкой контрольной суммы |
| `POST /api/jobs` | `.job-request.6`, `inputs` или закреплённые `settings` |
| `GET /api/presets?input_ref=…` | Совместимые именованные наборы |
| `POST /api/presets` | `input_ref`, `name`, `inputs`, `expected_revision` |
| `POST /api/experiments` | Конечный состав `variants` или `grid` |
| `GET /api/experiments` | Последние 50 серий |
| `GET /api/experiments/ID?start=0&limit=25` | Страница вариантов, до 100 |
| `POST /api/experiments/ID/pause,resume,cancel` | Один выбранный action, тело `{}` |

Создание серии требует `name`, `input_ref`, `snapshot_id`, `timeout_seconds`
и ровно одно из `variants`/`grid`. Дополнительно: базовые `inputs`, прежний
`primary_context`/`contexts`, `broker_settings`, `max_attempts` (1…3, default 2),
`checkpoint_interval_bars` (default 1000). Ошибочный вариант отклоняет весь состав.
Идентичные после нормализации варианты запрещены. До 256 вариантов на серию,
до 16 одновременно незавершённых работающих серий.

## Исполнение и восстановление

SQLite schema 9 хранит пресеты, неизменяемый manifest серии и планы всех вариантов.
Дочерний job и ссылка на него публикуются одной транзакцией. Диспетчер подаёт
задания в существующий JobManager, не создаёт отдельный пул. Обычные задания,
replay/live и варианты используют прежний общий бюджет процессов/потоков/памяти.
Одновременно очередных/работающих experiment jobs не больше лимита workers.

RunPlan сохраняет полные настройки; checkpoint проверяет их хеш до восстановления.
Retry использует сохранённый лимит попыток, пауза не меняет состав. После crash
восстанавливаются очередь и не созданные варианты; завершённый job остаётся
с прежним result ID и счётчиком попыток. Отменённую серию продолжить нельзя.

IR — `.typed-ir.6`, настроенный RunPlan — `.run-plan.5`, итог — `.run-result.5`
или `.run-summary.6`, runtime checkpoint — `.runtime-state.2`.
Запись настроенной live-сессии — `.session-recording.2`; импорт сверяет настройки
с программой до создания сессии. Старые формы без input сохраняют прежнее кодирование;
это не обещание совместимости исполнения при изменении computation/spec identity.

Backup/restore и сборка мусора учитывают ещё не опубликованные задания серии,
их контексты, IR и пресеты. Общая retention не удаляет jobs и снимки,
необходимые для сохранённого сравнения. Отдельное удаление серий в этот этап
не входит; отмена сохраняет их историю.

## Проверки

`tools.release_gate` включает 24 независимых input-примера для v1–v6 и проверки
типов, ограничений, CLI/HTTP, нативных workers, live/recording, пресетов,
атомарности, kill/retry, перезапуска и backup/restore. Ожидания корпуса заданы
вручную, не получены из runtime. `tools.install_check` повторяет цепочку
на установленном wheel вне checkout. Отдельная воспроизводимая аварийная проверка:

```sh
python -m tools.experiment_soak --directory NEW_PROOF_DIRECTORY
```

Она завершает один вариант, сохраняет второй в очереди, третий в manifest,
убивает реальный coordinator, создаёт backup, восстанавливает его и проверяет
ровно три результата без повторного исполнения первого варианта.
