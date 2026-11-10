# Размеры и типографика ячеек Pine

| Версии Pine | Параметры `table.cell()` и соответствующие `table.cell_set_*()` |
| --- | --- |
| v4–v6 | `width`, `height`, `text_halign`, `text_valign`, `text_size` со значениями `size.*` |
| v5–v6 | `tooltip` и `text_font_family` |
| v6 | Целый `text_size` в типографских пунктах и `text_formatting` |

В v1–v3 таблицы получают `TABLE_VERSION`. Именованный параметр
или setter, который появился позже версии скрипта, получает
`TABLE_CELL_LAYOUT_VERSION`. Позиционные параметры идут в порядке
закреплённой сигнатуры. После текста идут ширина, высота и цвет текста.
[Закреплённая спецификация](../spec/lock.json) · [Таблицы Pine](https://www.tradingview.com/pine-script-docs/visuals/tables/#creating-and-displaying-tables)

```pine
//@version=6
indicator("Status", overlay=false)
var table dash = table.new(position.top_right, 2, 1, border_width=1,
    border_color=color.gray, force_overlay=true)
if barstate.isfirst
    table.cell(dash, 0, 0, "Close", width=15, text_halign=text.align_left)
if barstate.islast
    table.cell(dash, 1, 0, str.tostring(close), width=18, height=12,
        text_size=16, text_font_family=font.family_monospace,
        text_formatting=text.format_bold, tooltip="Цена закрытия")
```

В OpenPine ширина и высота принимают значения от 0 до 100, включая дробные.
Ноль оставляет размер автоматическим. Если в колонке есть положительная
ширина, берётся наибольшая заданная ширина. Для высоты строки действует
то же правило. Проценты считаются от области таблиц на её панели, а текст,
который выходит за отведённый размер, обрезается. По умолчанию
используется `size.normal` (14 пунктов в интерфейсе OpenPine).
Числовой размер в v6 ограничен 256 пунктами, ноль включает авторазмер.
[Размеры ячеек в Pine](https://www.tradingview.com/pine-script-docs/visuals/tables/#sizing-table-cells) · [ADR 0043](adr/0043-table-cell-layout.md)

`table.cell()` целиком заменяет ячейку, а setter меняет одно поле.
Setter для пустой ячейки использует обычные умолчания для остальных полей.
Пустая строка и `na` скрывают подсказку. Подсказка отображается как обычный
текст, а управление прокруткой и перетаскиванием остаётся у графика.
[Изменение свойств в Pine](https://www.tradingview.com/pine-script-docs/visuals/tables/#modifying-table-and-cell-properties)

Оформление ячейки сохраняется в результате и в checkpoint. История и replay
используют одну отрисовку. Открытый бар может временно изменить размеры и
подсказку в предпросмотре. Ранее сохранённые результаты и checkpoint читаются
в исходном формате. [Внутренние сценарии](../conformance/tables/cases.json)
