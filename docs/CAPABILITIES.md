# Матрица возможностей OpenPine v1–v6

Это карта ограниченных реализованных профилей. `spec/coverage.json` считает
импортированные записи справочника; наличие записи в wiki не означает
исполнение этого API. Число conformance-сценариев также не является процентом
совместимости со всем Pine. Точное сравнение с TradingView выделено отдельно.

`python -m tools.capabilities --json` строит инвентаризацию из допущенного
компилятором каталога, текущих profile IDs и реальных `cases.json`. Она
показывает имена, overload ID, операции, параметры, ограничения qualifiers,
ссылки на cases и compatibility notes. Команда ничего не записывает и не
заявляет, что тесты выполнены. Input/statement/function special forms
описаны отдельными ссылками в её отчёте.

| Возможность | v1–v4 | v5–v6 | Профиль и проверка |
| --- | --- | --- | --- |
| Объявление индикатора, plot | `study`, `plot` | `indicator`, `plot` | `pineN.historical-core.1` (v1–v5), `pine6.core-mvp.1`; [versions](../conformance/versions/cases.json) |
| Переменные, `:=`, if/else, var, `?:`, арифметика | Принятый версионный срез | Принятый версионный срез; v6 bool не допускает na | `openpine.language.vN.1`; [language](../conformance/language/cases.json), [правила](STATEMENTS.md) |
| OHLCV, история, na/nz, abs, round, SMA/EMA, change/cross | Глобальные имена | `math.*`/`ta.*` | `openpine.series.vN.1` и core; [series](../conformance/series/cases.json), [правила](SERIES.md) |
| Пользовательские функции и возвращающие блоки | `=>`, собственное состояние вызовов | То же | `openpine.functions.vN.1`; [functions](../conformance/functions/cases.json) |
| Параметры | `input`, ограничения имён/type зависят от версии | `input` и типизированные `input.*` | `openpine.inputs.vN.1`; [inputs](../conformance/inputs/cases.json), [правила](INPUTS.md) |
| Контекстные запросы | `security`; v1/v2 positional, default lookahead on | `request.security`; default lookahead off | `openpine.requests.vN.6`; [contexts](../conformance/contexts/cases.json), [граф зависимостей/UDF](CONTEXT_GRAPH.md), [новый корпус](../conformance/context-graph/cases.json) |
| Market-входы, закрытия, капитал | `strategy.entry/close`; v1 направление bool | Направления `strategy.long/short`; v6 без when | `openpine.strategy.vN.1`; [strategies](../conformance/strategies/cases.json) |
| Stop/limit, изменение/отмена | `strategy.exit/cancel/cancel_all` | То же; v6 exit без when | `openpine.exits.vN.1`, `openpine.protective-broker.1`; [exits](../conformance/exits/cases.json), [правила](PROTECTION.md) |
| Частичные close/exit, несколько bracket | Явные qty/qty_percent; ранние пробелы сигнатур реконструированы | Те же количества; v6 без when | `openpine.partial-exits.vN.1`, `openpine.partial-broker.1`; [корпус](../conformance/partial-exits/cases.json), [правила](PARTIAL_EXITS.md) |
| Отложенные входы | Limit/stop/stop-limit; пробелы v1/v2 реконструированы | Те же виды; v6 без when | `openpine.pending-entries.vN.1`, `openpine.pending-entry-broker.1`; [корпус](../conformance/pending-entries/cases.json), [правила](PENDING_ENTRIES.md) |
| Лоты и pyramiding | Заголовок v1 и выбор закрытия v1–v3 реконструированы; FIFO/ANY | Те же ограниченные правила; when только в v5 | `openpine.lots.vN.1`, `openpine.lot-broker.1`; [корпус](../conformance/lots/cases.json), [правила](LOTS.md) |
| Составные результаты, variance/stdev, MACD/BB | Распаковка и глобальные имена; BB v1–v3 реконструирован | Распаковка и `ta.*`; biased у статистики | `openpine.composite.vN.1`; [composite](../conformance/composite/cases.json), [правила](COMPOSITE.md) |
| RMA/TR/ATR/RSI/highest/lowest | Глобальные имена; v1/v2 positional | Имена `ta.*` | `openpine.technical.vN.1`; [technical](../conformance/technical/cases.json), [правила](TECHNICAL.md) |
| Циклы и управление | v1: отказ; v2–v4: `for`, `break`, `continue` | Также `while`; v6 динамическая конечная граница | `openpine.loops.vN.1`; [loops](../conformance/loops/cases.json), [правила](LOOPS.md) |
| Массивы примитивов и ссылки | v1–v3: отказ; v4: new_*, операции и история | Также generic-синтаксис и for…in; отрицательные get/set индексы в v6 | `openpine.arrays.vN.1`; [arrays](../conformance/arrays/cases.json), [правила](ARRAYS.md) |
| События и взвешенные окна | barssince/valuewhen, cum/sum, WMA/VWMA, mom/roc, rising/falling, highestbars/lowestbars | Имена ta.*, math.sum, bool без na в v6 | `openpine.signals.vN.1`; [signals](../conformance/signals/cases.json), [правила](SIGNALS.md) |
| Время и окружение | UTC-поля с v1; timestamp с v2; barstate по версии, time_close с v4 | syminfo/timeframe, явная граница прогрева, закреплённый шаг цены | `openpine.environment.vN.1`; [environment](../conformance/environment/cases.json), [правила](ENVIRONMENT.md) |

`N` — конкретная версия 1…6, а не автоматическое наследование общей семантики.
Ограничения и принятые реконструкции находятся в immutable bundle и
compatibility notes. Например, EMA v1–v4 использует SMA seed, v5/v6 — первое
определённое значение; RSI/RMA нового среза имеют отдельные явные правила.
Поддержка одного overload не означает поддержку всех перегрузок функции.

| Граница исполнения | Текущий объём | Основные проверки |
| --- | --- | --- |
| История и контексты | Подтверждённые бары, UTC/24x7, минуты и D/W/M, same/higher timeframe, до 8 контекстов/16 запросов | `test_contexts.py`, `test_calendar.py`, `test_request_expressions.py` |
| Подготовка приложения | Общий закреплённый граф файлов/снимков/провайдера, покрытие и окно оценки, повтор и восстановление | `test_launches.py`, `test_launch_http.py`, [запуск](LAUNCH.md) |
| Вычислительное состояние | Отдельное на call site/контекст, ограниченные окна, checkpoint и rollback | `test_checkpoints.py`, `test_realtime.py`, `test_technical_state.py`, `test_composite_state.py` |
| Торговля | Одна агрегированная позиция, до 16 однонаправленных лотов в новом профиле; фиксированное количество, market/limit/stop/stop-limit, до 16 pending entries и 16 bracket; подтверждённая OHLC-модель | `test_lots.py`, `test_lots_pipeline.py`, `test_pending_entries.py`, `test_partial_broker.py` |
| Прикладные стратегии | 103 стратегии в technical/composite/loops/arrays/environment/signals/context-graph/partial-exits/pending-entries/lots, общие worker/график/ledger/search/walk-forward | `test_lots_pipeline.py`, [lots](../examples/lots/), [pending-entries](../examples/pending-entries/), [context-graph](../examples/context-graph/) |
| Хранение и поставка | SQLite 3.54.1, закреплённый DuckDB 2.0 dev, Parquet; Python 3.14.8 | `tools.release_gate`, `tools.install_check`, [приёмка](LOTS_PLAN.md) |

За пределами текущего среза: все остальные TA/strategy API и их перегрузки,
динамические/вложенные запросы и lower-TF, произвольные captures в request,
вложенные коллекции/tuples, tuple-параметры, произвольные OCA-группы, встроенные trailing
заявки и защитное исполнение на наблюдаемых незакрытых свечах. Неподдержанные
конструкции должны давать явную диагностику.
