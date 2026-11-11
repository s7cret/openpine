# Оповещения Pine

`alertcondition(condition, title, message)` доступен с Pine v2.
Глобальный вызов объявляет именованное условие индикатора. Заголовок и шаблон
сообщения вычисляются при компиляции. Сервис возвращает их в `alert_conditions`.
В стратегии вызов допустим в глобальной области. События стратегии формирует
`alert()`. В v2–v3 сигнатура опирается на архивный пример и справочник.
[Журнал версий Pine](https://www.tradingview.com/pine-script-docs/v4/release-notes/#january-2016) ·
[Условия Pine](https://www.tradingview.com/pine-script-docs/concepts/alerts/#alertcondition-events)

`alert(message, freq)` доступен в индикаторах и стратегиях Pine v4–v6.
Сообщение может вычисляться на каждом обновлении. Частота выбирается из
`alert.freq_once_per_bar` (по умолчанию), `alert.freq_once_per_bar_close`
и `alert.freq_all`. Для стратегии с расчётом на закрытии применяется частота
закрытия. При `calc_on_every_tick=true` действует указанная частота.
[Справка Pine v4](https://www.tradingview.com/pine-script-docs/v4/essential/alerts/) ·
[Частота в Pine v6](https://www.tradingview.com/pine-script-docs/concepts/alerts/#alert-function-events)

```pine
//@version=6
indicator("Breakout", overlay=true)
var line level = line.new(0, 10, 3, 10)
alertcondition(close > 10, "Above level", "Breakout")
if close > 10
    alert("Price " + str.tostring(close), alert.freq_once_per_bar_close)
plot(close)
```

Записанный replay автоматически наблюдает условия индикатора на закрытии.
События `alert()` привязаны к наблюдению, в котором выполнен вызов.
Для `once_per_bar` OpenPine считает первое достижение каждого места вызова
в пределах свечи. Историческое исполнение вычисляет аргументы и метаданные.
События журнала формируются на наблюдаемой части replay. Шаблон условия
передаётся в журнал текстом. [ADR 0047](adr/0047-replay-alert-journal.md)

`GET /api/replays/{id}/alert_events?start=0&limit=100` читает события по порядку.
Каждая запись включает номер и время входного события, порядковый номер
вызова, свечу, фазу и сообщение. Текст ограничен 4 096 байтами UTF-8,
один расчёт принимает до 64 достигнутых оповещений. Контракт охватывает
локальный журнал. Сравнение с TradingView проводится отдельно.
[Сценарии по версиям](../conformance/alerts/cases.json)
