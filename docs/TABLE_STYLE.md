# Оформление и перенос таблиц Pine

В Pine v4–v6 `table.new()` принимает именованные `frame_color`,
`frame_width`, `border_color` и `border_width`. Рамка проходит по периметру
таблицы, а линии сетки проходят между ячейками. Цвет `na` прозрачен.
Ширина задаётся целым числом от 0 до 64 пикселей в профиле OpenPine.
Пропущенные цвета получают `na`, ширина по умолчанию равна нулю.
В v5–v6 оформление сочетается с именованным `force_overlay=true`.
[Справка Pine](https://www.tradingview.com/pine-script-docs/visuals/tables/#creating-and-displaying-tables)

```pine
//@version=6
indicator("Status", overlay=false)
var table dash = table.new(position.top_right, 2, 1,
    frame_color=#AACCEE, frame_width=2, border_color=#557799,
    border_width=1, force_overlay=true)
if barstate.isfirst
    table.cell(dash, 0, 0, "Close")
if barstate.islast
    table.set_position(dash, position.bottom_left)
    table.set_bgcolor(dash, #112233)
    table.cell_set_text(dash, 1, 0, str.tostring(close))
    table.cell_set_text_color(dash, 1, 0, color.white)
```

`table.set_bgcolor()`, `table.set_frame_color()`, `table.set_frame_width()`,
`table.set_border_color()` и `table.set_border_width()` меняют по одному
свойству. Пропуск нового значения сбрасывает это свойство к умолчанию.
`table.cell_set_text()`, `table.cell_set_text_color()` и
`table.cell_set_bgcolor()` сохраняют остальные поля. Если ячейка пуста
или очищена, setter создаёт её с обычными умолчаниями `table.cell()`
для остальных полей. Повторный `table.cell()` целиком заменяет ячейку.
Индексы начинаются с нуля. Выход за границу получает `TABLE_INDEX`.
[Справка Pine](https://www.tradingview.com/pine-script-docs/visuals/tables/#modifying-table-and-cell-properties)

Перенос в свободную позицию сохраняет ID, ячейки, стиль и панель.
При переносе в занятую позицию остаётся объект, созданный позже.
В OpenPine вытесненный ID удаляется из состояния и из следующего чтения
`table.all` в v5–v6. Вызовы через этот ID пропускаются. В Pine v1–v3
новые операции получают `TABLE_VERSION`.
[ADR 0042](adr/0042-table-style-and-relocation.md)

История и replay используют одну отрисовку. Открытый бар может временно
перенести или перекрасить таблицу в предпросмотре. Подтверждённое состояние
и предпросмотр сохраняются в checkpoint. Старые записи читаются в исходном
формате. Запись со стилем включает четыре дополнительных поля.
[Сценарии](../conformance/tables/cases.json)
