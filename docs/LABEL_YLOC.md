# Привязка метки к свече

`label.new` принимает режим Y в Pine v4–v6. `label.set_yloc` меняет
режим, а `label.set_y` меняет числовую координату. Перегрузка
`label.new` с `chart.point` доступна в v5–v6.
В v1–v3 действует `LABEL_VERSION`. Режим Y может вычисляться на каждом
баре. Неверный режим получает `LABEL_YLOC`.
[Сигнатуры](../spec/label-yloc-profile.json)

```pine
//@version=6
indicator("Last candle", overlay=true)
var label tag = label.new(bar_index, na, "Last", yloc=yloc.abovebar)
label.set_xy(tag, bar_index, close)
label.set_yloc(tag, close >= open ? yloc.abovebar : yloc.belowbar)
label.set_style(tag, close >= open ? label.style_label_down : label.style_label_up)
```

`yloc.price` рисует метку по её числовому Y. В режимах `yloc.abovebar`
и `yloc.belowbar` положение определяется по свече. `label.set_y` и
`label.set_xy` сохраняют новый Y и в этих режимах. При возврате в
`yloc.price` используется последнее записанное значение. Сеттеры
сохраняют ID и оформление. Прежние записи без `yloc` читаются как
`price`. [Позиционирование Pine](https://www.tradingview.com/pine-script-docs/visuals/text-and-shapes/#positioning-labels)

OpenPine использует `high` и `low` на ценовой панели и `close`, если
экстремум отсутствует. На отдельной панели метки размещаются у верхней
или нижней границы по тому же правилу, что и сигнальные метки. Для
`xloc.bar_time` в режимах над и под свечой требуется точное время её
открытия. Дробная проекция остаётся доступна в режиме `yloc.price`.

Окно истории и live-график определяют якорь по отображаемым свечам.
Когда меняется экстремум открытой свечи, предварительная метка может
двигаться без мутации объекта. В журнале и checkpoint сохраняются
исходный Y и режим. [ADR 0022](adr/0022-label-bar-anchors.md) ·
[Сценарии conformance](../conformance/label-yloc/cases.json)
