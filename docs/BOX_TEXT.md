# Текст ценовых зон Pine

В Pine v5–v6 оба варианта `box.new` принимают `text`, `text_size`,
`text_color`, `text_halign`, `text_valign`, `text_wrap` и
`text_font_family`. Каждый параметр можно изменить через
`box.set_text*`. В v6 доступны целочисленный размер и
`text_formatting` с `box.set_text_formatting`. Жирное и курсивное
начертания можно сложить. В v4 текстовые параметры получают
`BOX_TEXT_VERSION`, а сам `box` доступен. В v1–v3 действует
`BOX_VERSION`. [Закреплённые сигнатуры](../spec/box-text-profile.json) ·
[Справка Pine](https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/#creating-boxes)

```pine
//@version=6
indicator("Named price zone", overlay=true)
var box zone = box.new(0, high, 1, low, bgcolor=#33669940, text="Price zone",
     text_color=color.white, text_halign=text.align_left, text_valign=text.align_top,
     text_wrap=text.wrap_auto, text_font_family=font.family_monospace)
box.set_rightbottom(zone, bar_index + 1, low)
box.set_text_formatting(zone, text.format_bold + text.format_italic)
```

По умолчанию текст пустой, цвет `color.black`, размер `size.auto`,
выравнивание по центру обеих осей, шрифт обычный и без начертания.
Для размера доступны `size.*`, а в v6 ещё целое от 0 до
`2**63 - 1`. Ноль соответствует автоматическому размеру. График
ограничивает рисуемый размер 64 пунктами. Текст хранится целиком
до 4096 символов. Прозрачные рамка и фон допускают видимый текст.

При `text.wrap_auto` график переносит строки по ширине и обрезает
текст по границам зоны. Длинные слова делятся по видимым символам.
При `text.wrap_none` переносы строк становятся пробелами, а подпись
может выйти за границы зоны в пределах панели. Подпись привязана к
исходным углам и сохраняет положение при прокрутке. В окно она
попадает вместе с прямоугольником. Продление только рамки рисует
только рамку. Границы с нулевой площадью отображаются без текста.

Сеттеры сохраняют ID, координаты и рамку. Журнал, checkpoint и
предпросмотр хранят текстовые поля вместе с геометрией. Старые
записи боксов читаются в исходном формате. `force_overlay` принимает
значение `false` в этом профиле. [ADR 0024](adr/0024-box-text-layout.md) ·
[Сценарии conformance](../conformance/box-text/cases.json)
