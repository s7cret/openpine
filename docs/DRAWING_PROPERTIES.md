# Чтение и изменение отдельных свойств рисунков

Операции доступны в индикаторах Pine v4–v6. В v4 используется `study()`,
в v5–v6 — `indicator()`.

| Объект | Новые setters | Новые getters |
| --- | --- | --- |
| Линия | `line.set_x1()`, `line.set_x2()`, `line.set_y1()`, `line.set_y2()` | `line.get_y1()`, `line.get_y2()` |
| Метка | `label.set_x()` | `label.get_y()`, `label.get_text()` |
| Бокс | `box.set_left()`, `box.set_right()`, `box.set_top()`, `box.set_bottom()` | `box.get_top()`, `box.get_bottom()` |

Setter обновляет указанное поле общей записи объекта. Все ссылки на этот ID
видят новое значение. Для X нужен целый индекс бара или timestamp в режиме
текущего `xloc`. Для цены допускаются числовые значения и `na`. Смена режима
координат выполняется отдельной операцией. Изменение поля сохраняет ID,
оформление и порядок создания, по которому действует вытеснение.
[Линии и боксы](https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/#modifying-lines) · [Метки](https://www.tradingview.com/pine-script-docs/visuals/text-and-shapes/#labels) · [Режимы координат](DRAWING_XLOC_MUTATIONS.md)

Getter читает сохранённое поле. Например, `label.get_y()` возвращает
записанный Y и при `yloc.abovebar`, где экранная привязка берётся от свечи.
В OpenPine getter возвращает `na` для удалённого, вытесненного или
отсутствующего ID. Это относится и к `label.get_text()`. Живая метка с
пустым текстом возвращает `""`. Setter пропускает запись по отсутствующему ID.

Чтение не создаёт событий. Изменения открытого бара остаются в preview
до подтверждения. Снимок и журнал используют тот же формат записи.
Сверка пограничных случаев с TradingView остаётся отдельной проверкой.
[Сценарий v4](../conformance/linefills/v4-drawing-properties.pine) · [Копирование и вытеснение v6](../conformance/linefills/v6-drawing-properties-copy.pine) · [ADR 0041](adr/0041-drawing-property-access.md)
