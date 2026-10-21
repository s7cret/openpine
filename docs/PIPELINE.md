# Исторический путь OpenPine

Требуется Python **3.14.8**. [План и приёмка](PIPELINE_PLAN.md).

## CLI и переносимая компиляция

```sh
python -m pip install -r requirements-dev.txt
python -m apps.openpine run --source examples/pipeline/v6.pine --bars examples/pipeline/bars.json
python -m apps.openpine compile --source examples/pipeline/v6.pine --profile historical --output compiled.json
python -m apps.openpine run --artifact compiled.json --bars examples/pipeline/bars.json --output result.json
```

В `result.outputs` находятся три ряда: `[10,40,85,135]`, `[1,3,4,5]` и
`[0,10,20,30]`. Программы v1–v5 дают те же значения на этих входах. Наблюдения
не вычисляются заново для формирования ожидаемых таблиц.

`compile` сохраняет прежние поля ответа и добавляет `artifact`. Самостоятельный
артефакт — этот объект с версией `openpine.program-artifact.1`. Загрузка принимает
его или полный ответ CLI `compile`. Артефакт содержит типизированные закрытые
JSON-записи с явными тегами, хеш программы и identity. Он не содержит
исполняемого Python. Исходный Pine при загрузке артефакта не компилируется.

Проверяются поля и типы записей, хеш содержимого, версии Pine/IR, spec lock,
семантический профиль и хеш реализации; затем runtime проверяет весь граф IR до
первого эффекта. Хеш обеспечивает целостность, а не удостоверяет автора.
Артефакты привязаны к установленной реализации; после её изменения их следует
скомпилировать заново. Повторное исполнение создаёт новое состояние.

`Application` в `packages.execution.service` обслуживает CLI, а далее worker и
HTTP. `CompileRequest` / `RunRequest` и статусы задания определены в contracts.
Диагностика сохраняет категорию, код, Pine-версию и позицию в исходнике.

## Исторические данные

`HistoricalProvider.fetch(DataQuery)` возвращает `MarketSnapshot`. `FileProvider`
читает существующие `.1` close-only и `.2` OHLCV-файлы. Запрос явно задаёт symbol,
timeframe и диапазон **[start_ms,end_ms)** по времени открытия в UTC. Другой
symbol/timeframe, пустой диапазон или отсутствие баров дают диагностированный
отказ. Пропуски сохраняются, бары не достраиваются и не ресемплируются. Окна
индикаторов начинают прогрев с первого выбранного бара; дополнительные бары
для прогрева необходимо включить в запрошенный диапазон самостоятельно.

Снимок содержит нормализованные данные, хеш данных, provenance запроса и хеш
всего снимка. `SnapshotStore` сохраняет снимки по хешу и проверяет целостность
при чтении. Изменение диапазона/данных создаёт другую идентичность. Вычисления
получают неизменяемые данные и не обращаются к провайдеру внутри бара.

## Отдельный worker

```sh
python -m apps.openpine job --source examples/pipeline/v6.pine --bars examples/pipeline/bars.json --output job-result.json
python -m apps.openpine job --artifact compiled.json --bars examples/pipeline/bars.json --timeout 30
```

Команда ждёт завершения задания; Ctrl+C отменяет его. `--state-dir` по умолчанию
`.runtime/jobs`. Эта папка принадлежит одному приложению одновременно (ОС-lock).
Статусы, запрос, прогресс и завершённый ответ сохраняются атомарно. Результат
проверяется по хешу при чтении. Статусы: queued, running, succeeded, failed,
cancelled, timed_out. Ошибка компиляции — failed с исходной диагностикой.

Один subprocess обслуживает одно задание; по умолчанию до двух одновременно,
очередь до 32 незавершённых заданий. Время запуска/компиляции входит в timeout
(0 < timeout <= 300 секунд). Supervisor принудительно завершает процесс при
отмене/таймауте. Частичные результаты не выдаются. Прогресс обновляется раз в
100 баров и на последнем баре. Новый процесс всегда начинает с пустым состоянием.
Worker следит за смертью хозяина; после перезапуска незавершённые статусы
становятся failed/HOST_RESTARTED. Завершённые результаты остаются доступны.
Размер сохранённого ответа ограничен 32 MiB. subprocess изолирует состояние и
сбои, но не предоставляет ОС-песочницу. Проверки: `tests/test_jobs.py`.

## Реальные котировки и офлайн-повтор

```sh
python -m apps.openpine fetch --provider binance --symbol BINANCE:BTCUSDT --timeframe 60 --start-ms 1704067200000 --end-ms 1704153600000 --output btc.json
python -m apps.openpine job --source examples/pipeline/v6.pine --bars btc.json --output btc-result.json
```

Первый вызов получает 24 часовых бара 2024-01-01, сохраняет content-addressed
снимок в `.runtime/snapshots` и экспортирует его в `btc.json`. Второй и следующие
запуски используют файл офлайн. Веб и CLI используют один `BinanceProvider`.

Поддерживается публичный **Binance Spot** через `data-api.binance.vision`, без
ключей. Канонические интервалы: 1/3/5/15/30/60/120/240/360/480/720, D/W/M.
Параметры и 12 полей kline сверены с [официальным API](https://developers.binance.com/en/docs/catalog/core-trading-spot-trading/api/rest-api/market).
[Публичный endpoint](https://developers.binance.com/en/docs/products/spot/rest-api)
предназначен для рыночных данных. Проверено 2026-10-21.

Источник старого подхода: `origin/main` на `af697b2`, файлы
`openpine/data/direct_data_provider.py` и `direct_provider.py`. Сохранены идеи
отдельного адаптера, явного запроса и пагинации. Реализация R6 использует общие
контракты монорепозитория. Курсор продвигается по фактическому open time + 1 ms;
подтверждённость определяется **close time из ответа < server time**, включая
месячные бары. Ошибки сети/данных не превращаются в частичный успех. Дубликаты,
нарушение порядка, неверные OHLCV и неподдержанные запросы отклоняются.

Диапазон задаётся временем открытия; end должен быть не позже server time.
Пропуски/начало листинга сохраняются без дополнения; фактические времена доступны
в снимке. Provenance хранит запрос, время сервера, endpoint и хеш каждой страницы.
Лимиты: 100000 баров, 101 страница, 60 секунд между началом пагинации и стартом
следующей страницы; отдельный HTTP-запрос имеет timeout 10 секунд. При HTTP
ошибке запрос завершается явно, автоматического частичного кэша/повтора нет.
Офлайн gate использует собственные подставные страницы (`test_binance.py`),
доступность биржи проверяется отдельно командой выше.

Проверки: `python -m unittest discover -s tests -p "test_*application*.py" -v`,
`test_pipeline.py`, `test_providers.py`, `test_artifacts.py`; общий gate:
`python -m tools.release_gate`.
