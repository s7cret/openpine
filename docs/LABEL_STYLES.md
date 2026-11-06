# Оформление ценовых меток Pine

`label.new` принимает `color`, `style`, `textcolor` и `size` в координатной
перегрузке v4–v6 и в перегрузке с `chart.point` v5–v6. С v4 доступны
`label.set_color`, `label.set_style`, `label.set_textcolor` и `label.set_size`.
В v1–v3 действует `LABEL_VERSION`. Профиль ограничен `yloc.price` и поддерживает
оба режима `xloc` из профиля рисунков. [Сигнатуры](../spec/label-styles-profile.json)

```pine
//@version=6
indicator("Styled close", overlay=true)
var label tag = label.new(0, close, "Close", color=#123456A0, style=label.style_label_up)
label.set_xy(tag, bar_index, close)
label.set_textcolor(tag, color.white)
label.set_size(tag, 18)
```

`label.style_none` рисует только текст. У `label.style_label_center` ценовая
точка в центре плашки. Варианты `label.style_label_up`, `label.style_label_down`,
`label.style_label_left` и `label.style_label_right` задают направление
указателя. Цвет фона и текста независимы, поддерживают RGBA и `na`. Canvas
обрезает рисунок по границам панели и сохраняет исходную точку указателя.
[Позиционирование меток Pine](https://www.tradingview.com/pine-script-docs/visuals/text-and-shapes/#positioning-labels)

Все три версии принимают `size.auto`, `size.tiny`, `size.small`,
`size.normal`, `size.large` и `size.huge`. В v6 можно задать
неотрицательное целое число в типографских пунктах. Число `0` и `size.auto`
дают 12 пунктов в OpenPine. Canvas ограничивает размер 64 пунктами,
а сохранённая запись удерживает исходное число. В v4–v5 число отклоняется с
`ARGUMENT_TYPE`. Неверные значения оформления получают `LABEL_STYLE` или
`LABEL_SIZE`. [Размеры в справке Pine](https://www.tradingview.com/pine-script-docs/visuals/text-and-shapes/#text-formatting-and-size)

По умолчанию оформленная метка имеет синюю плашку с указателем вниз,
белый текст и размер `size.normal`. Если пропустить размер в `label.set_size`,
применится `size.auto` в v4–v5 и `size.normal` в v6. Сеттеры сохраняют
ID метки, в том числе при вызове через псевдоним. Старые записи сохраняют
свой вид до первого сеттера оформления. Изменения открытой свечи видны в
предпросмотре, а журнал хранит подтверждённые события. [ADR 0021](adr/0021-label-appearance-record.md) ·
[Сценарии conformance](../conformance/label-styles/cases.json)
