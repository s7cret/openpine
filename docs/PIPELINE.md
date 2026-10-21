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

Проверки: `python -m unittest discover -s tests -p "test_*application*.py" -v`,
`test_pipeline.py`, `test_providers.py`, `test_artifacts.py`; общий gate:
`python -m tools.release_gate`.
