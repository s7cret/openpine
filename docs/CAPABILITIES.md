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
| Контекстные запросы | `security`; v1/v2 positional, default lookahead on | `request.security`; default lookahead off | `openpine.requests.vN.3`; [contexts](../conformance/contexts/cases.json), [правила](CONTEXTS.md) |
| Market-входы, закрытия, капитал | `strategy.entry/close`; v1 направление bool | Направления `strategy.long/short`; v6 без when | `openpine.strategy.vN.1`; [strategies](../conformance/strategies/cases.json) |
| Stop/limit, изменение/отмена | `strategy.exit/cancel/cancel_all` | То же; v6 exit без when | `openpine.exits.vN.1`, `openpine.protective-broker.1`; [exits](../conformance/exits/cases.json), [правила](PROTECTION.md) |
| RMA/TR/ATR/RSI/highest/lowest | Глобальные имена; v1/v2 positional | Имена `ta.*` | `openpine.technical.vN.1`; [technical](../conformance/technical/cases.json), [правила](TECHNICAL.md) |

`N` — конкретная версия 1…6, а не автоматическое наследование общей семантики.
Ограничения и принятые реконструкции находятся в immutable bundle и
compatibility notes. Например, EMA v1–v4 использует SMA seed, v5/v6 — первое
определённое значение; RSI/RMA нового среза имеют отдельные явные правила.
Поддержка одного overload не означает поддержку всех перегрузок функции.

| Граница исполнения | Текущий объём | Основные проверки |
| --- | --- | --- |
| История и контексты | Подтверждённые бары, UTC/24x7, минуты и D/W/M, same/higher timeframe, до 8 контекстов/16 запросов | `test_contexts.py`, `test_calendar.py`, `test_request_expressions.py` |
| Вычислительное состояние | Отдельное на call site/контекст, ограниченные окна, checkpoint и rollback | `test_checkpoints.py`, `test_realtime.py`, `test_technical.py`, `test_technical_state.py` |
| Торговля | Одна позиция, фиксированное количество, одна full-position bracket; next-bar OHLC, stop-first при неизвестном двойном касании | `test_strategies.py`, `test_protection.py`, `test_protection_pipeline.py` |
| Прикладные стратегии | 18 примеров, общие worker/график/ledger/search/walk-forward | `test_technical_pipeline.py`, [examples/technical](../examples/technical/) |
| Хранение и поставка | SQLite 3.54.1, закреплённый DuckDB 2.0 dev, Parquet; Python 3.14.8 | `tools.release_gate`, `tools.install_check`, [план приёмки](TECHNICAL_PLAN.md) |

За пределами текущего среза: все остальные TA/strategy API и их перегрузки,
динамические/вложенные запросы и lower-TF, произвольные captures в request,
arrays/tuples, partial exits, pyramiding, OCA-группы, встроенные trailing
заявки и защитное исполнение на наблюдаемых незакрытых свечах. Неподдержанные
конструкции должны давать явную диагностику.
