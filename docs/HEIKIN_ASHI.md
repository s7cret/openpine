# Исторические запросы Heikin Ashi

| Pine | Вызов | Покрытие OpenPine |
| --- | --- | --- |
| v1 | — | Имя отсутствует в закреплённом справочнике. |
| v2 | `heikinashi(symbol)` | PARTIAL. Позиционная историческая реконструкция. |
| v3–v4 | `heikinashi(symbol)` | PARTIAL. Фиксированный тикер для `security()`. |
| v5–v6 | `ticker.heikinashi(symbol)` | PARTIAL. Фиксированный тикер для `request.security()`. |

В v2 запись Wiki подтверждает имя, а архивный скрипт показывает один
позиционный аргумент и передачу результата в `security()`.
Для v3–v6 сигнатуры сверены с закреплёнными справочниками.
[Пример v2](https://gist.github.com/bongkook/c204944f0d8956d29183dd446f6cdc85) ·
[Документация v3](https://www.tradingview.com/pine-script-docs/v3/essential/non-standard-chart-types-data/) ·
[Документация v4](https://www.tradingview.com/pine-script-docs/v4/essential/non-standard-chart-types-data/) ·
[Документация v6](https://www.tradingview.com/pine-script-docs/concepts/non-standard-charts-data/).

Поддерживаются прямой вызов и цепочка неизменяемых глобальных алиасов.
Рынок и таймфрейм вычисляются до чтения данных из литералов, закреплённых
входов и разрешённых свойств основного контекста. Допускаются тот же или
старший таймфрейм. Преобразование применяется к свечам интервала запроса.
Обычный и HA-запрос могут использовать один закреплённый снимок. Каждый
запрос применяет свои `gaps` и `lookahead` по правилам версии Pine.

Для первой свечи `HA open = (open + close) / 2`. Для каждой следующей берётся
среднее предыдущих `HA open` и `HA close`. `HA close` равен среднему четырёх
рыночных цен. Границы `HA high` и `HA low` учитывают тело HA и соответствующие
рыночные экстремумы. Объём и время берутся из исходной свечи. Пустой интервал
сохраняет предыдущее состояние преобразования. При отсутствующей цене в
присутствующей свече запрос завершится диагностикой `HEIKIN_ASHI_OHLC`.
[Формулы TradingView](https://www.tradingview.com/support/solutions/43000619436-understanding-heikin-ashi-charts/) ·
[ADR-0077](adr/0077-heikin-ashi.md).

В [индикаторе](../examples/heikin-ashi/compare.pine) рыночный close и HA OHLCV
показаны на одном наборе данных. В [стратегии](../examples/heikin-ashi/entry.pine)
HA close формирует условие входа, а исполнение использует рыночный open
следующей свечи. Примеры можно запустить с [фикстурой свечей](../conformance/heikin-ashi/bars.json).

Младшие интервалы v5–v6, replay, live и восстановление HA описаны в
[профиле рыночных тикеров](MARKET_TICKERS.md). Динамические series-тикеры
и синтетический основной график остаются за границами профиля. Точное
совпадение с TradingView требует отдельной внешней проверки.
