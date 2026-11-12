# Оповещения Pine

`alertcondition(condition, title, message)` доступен с Pine v2.
Глобальный вызов объявляет именованное условие индикатора. Заголовок и шаблон
сообщения вычисляются при компиляции. Сервис возвращает их в `alert_conditions`.
В стратегии вызов допустим в глобальной области. В v2–v3 сигнатура опирается
на архивный пример и справочник.
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
передаётся в журнал текстом в базовом профиле. [ADR 0047](adr/0047-replay-alert-journal.md)

`GET /api/replays/{id}/alert_events?start=0&limit=100` читает события по порядку.
Каждая запись включает номер и время входного события, порядковый номер
вызова, свечу, фазу и сообщение. Текст ограничен 4 096 байтами UTF-8,
один расчёт принимает до 64 достигнутых оповещений. Контракт охватывает
локальный журнал. Сравнение с TradingView проводится отдельно.
[Сценарии по версиям](../conformance/alerts/cases.json)

## Подстановки в `alertcondition()`

При создании replay индикатора Pine v4–v6 передайте
`"alert_condition_profile": "openpine.alertcondition-templates.1"` в запросе
`POST /api/replays` или в файле контекстов CLI. Поле `message` в `alert_conditions`
показывает исходный шаблон. Запись `alertcondition` в `alert_events` содержит
готовый текст после закрытия наблюдаемой свечи.

- `{{open}}`, `{{high}}`, `{{low}}`, `{{close}}` и `{{volume}}` читают свечу
  закрытия. `{{time}}` использует время открытия свечи, `{{timenow}}` — время
  события закрытия из записанной ленты. Оба времени выводятся в UTC до секунды.
- `{{exchange}}` и `{{ticker}}` берутся из квалифицированного символа основного
  контекста, `{{interval}}` — из его таймфрейма.
- `{{plot("Имя")}}` читает единственный обычный `plot()` с этим заголовком.
  Индексы `{{plot_0}}`–`{{plot_19}}` доступны для начальной последовательности
  обычных `plot()` в порядке исходника. Значения берутся из расчёта этой свечи,
  включая `display.none` и вызовы `plot()` после объявления условия.

Пропуск в числовом ряду подставляется как `na`. Неизвестные подстановки
сохраняются буквальным текстом. Недопустимый индекс отклоняется при создании
replay, отсутствующее или неоднозначное имя — при срабатывании. Лимит готового
сообщения составляет 4 096 байт UTF-8. Оповещения `alert()` и `order_fill`
используют собственные сообщения. Версии v2–v3 используют базовый профиль.
[Справка Pine](https://www.tradingview.com/pine-script-docs/concepts/alerts/#placeholders) ·
[ADR 0051](adr/0051-alertcondition-templates.md)

## Исполнения заявок

В Pine v1–v6 записанный replay поддерживает события исполнения
`strategy.entry()`, `strategy.order()`, `strategy.close()`,
`strategy.close_all()` и `strategy.exit()`. В запросе создания replay для
базового сообщения можно задать `"order_fill_alerts": true`. Любой
поддерживаемый вызов с `alert_message` или `disable_alert` включает профиль
автоматически. Для отложенных и защитных заявок, `strategy.order()`
и `strategy.close_all()` используется профиль
`openpine.observed-broker.1`.

- В v4–v6 `alert_message` вычисляется на наблюдении исполнения.
  Поддерживаются литералы, OHLC, `volume`, `bar_index`, `time`,
  скалярные `input`, простые неизменяемые глобальные выражения,
  глобальные скаляры `var`, скалярные операторы и
  `tostring` в v4 или `str.tostring` в v5–v6.
  Пропущенное сообщение даёт пустую строку.
- В v5–v6 `disable_alert` вычисляется при создании каждой заявки.
  Замена заявки получает собственное значение. Отменённая заявка события
  исполнения не создаёт.
- В v5–v6 `strategy.exit()` может задать `alert_profit` для тейк-профита,
  `alert_loss` для стоп-лосса и `alert_trailing` для трейлинг-стопа.
  Если задано `na` или параметр пропущен,
  используется `alert_message`. Явная пустая строка остаётся пустой.

Чистые глобальные выражения читают свечу наблюдения исполнения.
При исполнении до расчёта сообщение видит `var` последней подтверждённой
свечи. Исполнение через `process_orders_on_close` видит значения после
обычного расчёта закрытия. Эти же правила действуют для специальных
сообщений `strategy.exit()`. [ADR 0050](adr/0050-fill-alert-bindings.md)

Запись `order_fill` в `alert_events` содержит `order_id` и `fill_id`
из торгового журнала. Внутри одного наблюдения она занимает место
фактического исполнения среди вызовов `alert()`. При перезапуске worker
возобновляет журнал и восстанавливает флаги ожидающих заявок. Профиль
охватывает локальный replay и использует существующую модель брокера.
[ADR 0048](adr/0048-strategy-fill-alerts.md) ·
[ADR 0049](adr/0049-protective-fill-alerts.md) ·
[Оповещения об исполнении в Pine](https://www.tradingview.com/pine-script-docs/concepts/alerts/#order-fill-events)
