# Объединение ячеек таблиц Pine

В Pine v5–v6 функция `table.merge_cells(table_id, start_column,
start_row, end_column, end_row)` объединяет прямоугольник ячеек с
включёнными границами. Вызывать её можно до или после заполнения таблицы.
Одна ячейка остаётся без изменений. Индексы начинаются с нуля.
[Справка Pine](https://www.tradingview.com/pine-script-docs/visuals/tables/#merging-cells)

```pine
//@version=6
indicator("Status", overlay=true)
var table dash = table.new(position.top_right, 2, 2, border_width=1,
    border_color=color.gray)
if barstate.isfirst
    table.merge_cells(dash, 0, 0, 1, 0)
    table.cell(dash, 0, 0, "Market", bgcolor=color.blue, text_color=color.white,
        tooltip="Сводка индикатора")
    table.cell(dash, 0, 1, "Close", width=10)
if barstate.islast
    table.cell(dash, 1, 1, str.tostring(close), width=10)
```

Видимое содержимое, оформление и подсказка берутся из верхней левой
ячейки. Размер объединения складывается из размеров охваченных строк и
колонок. Для расчёта дорожек используются соседние ячейки, а дорожки без
явного размера подбираются автоматически. Сетка проходит по внешней
границе объединения. Эти правила действуют на панели индикатора и на
ценовом графике с `force_overlay`. [Справка Pine](https://www.tradingview.com/pine-script-docs/visuals/tables/#merging-cells)

`table.clear()` удаляет данные в запрошенных координатах. Если
очищена верхняя левая ячейка, объединение скрывается до её повторного
заполнения, а его диапазон сохраняется. Данные остальных ячеек
прямоугольника остаются скрытыми. Перенос таблицы сохраняет её
объединения. Удаление и замена таблицы удаляют её диапазоны вместе с ID.
[ADR 0044](adr/0044-table-merge.md)

Для Pine v1–v3 выдаётся `TABLE_VERSION`, для v4 —
`TABLE_MERGE_VERSION`. Выход за границы даёт `TABLE_INDEX`,
перевёрнутый прямоугольник — `TABLE_MERGE_RANGE`, пересечение или
повторное объединение — `TABLE_MERGE_OVERLAP`.

Объединения входят в результат и checkpoint. Предпросмотр открытого
бара может содержать свой набор объединений. Старые результаты и
checkpoint без диапазонов читаются в исходной форме.
[Сценарии](../conformance/tables/cases.json)
