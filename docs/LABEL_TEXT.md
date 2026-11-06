# Текст и подсказки меток Pine

В Pine v4–v6 доступны `textalign`, `tooltip`, `label.set_textalign`
и `label.set_tooltip`. Поддерживается выравнивание по левому краю,
центру и правому краю. С v5 есть `text_font_family` и
`label.set_text_font_family` для обычного и моноширинного шрифтов.
В v6 `text_formatting` и `label.set_text_formatting` принимают
`text.format_none`, `text.format_bold` и `text.format_italic`.
Два начертания можно сложить. В v1–v3 действует `LABEL_VERSION`.
[Закреплённые сигнатуры](../spec/label-text-profile.json) ·
[Форматирование в Pine](https://www.tradingview.com/pine-script-docs/visuals/text-and-shapes/#text-formatting)

```pine
//@version=6
indicator("Last bar hint", overlay=true)
var label tag = label.new(bar_index, na, "Last bar\nPrice",
     yloc=yloc.abovebar, textalign=text.align_left, text_font_family=font.family_monospace,
     text_formatting=text.format_bold + text.format_italic)
label.set_xy(tag, bar_index, close)
label.set_tooltip(tag, "Price at the latest bar")
```

По умолчанию текст выровнен по центру, шрифт обычный, без начертания.
Пустая подсказка отключает показ. Подсказка ограничена 4096 символами
и выводится обычным текстом с переносами строк. Сеттеры сохраняют ID
и текущие координаты. Настройки проходят через журнал, checkpoint
и предпросмотр replay. Старые записи открываются с прежним оформлением.

Canvas показывает подсказку верхней метки под курсором. Область
наведения учитывает плашку, указатель и обрезку панели. Для метки
без видимой плашки используются границы нарисованных строк.
При изменении окна или удалении метки подсказка пересчитывается
на текущей позиции курсора. В историческом графике при отсутствии
подсказки метки показаны данные свечи.

Конструкторы поддерживают сочетание текста с `chart.point`, `xloc`
и `yloc`. [Выбор панели](DRAWING_OVERLAY.md) ·
[ADR 0023](adr/0023-label-text-and-hover.md) ·
[Сценарии conformance](../conformance/label-text/cases.json)
