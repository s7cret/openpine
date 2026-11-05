# Рамки ценовых зон Pine

`box.new` принимает `border_width`, `border_style` и `extend` в
координатной перегрузке Pine v4–v6. Эти параметры доступны и
в перегрузке с `chart.point` для v5–v6. Функции
`box.set_border_width`, `box.set_border_style` и `box.set_extend`
работают с v4. В v1–v3 действует `BOX_VERSION`.
[Закреплённые сигнатуры](../spec/box-borders-profile.json)

```pine
//@version=6
indicator("Price range", overlay=true)
var box zone = box.new(0, high, 1, low, border_width=2, border_style=line.style_dashed, extend=extend.right, bgcolor=#33669930)
box.set_border_width(zone, close >= open ? 3 : 1)
```

Рамка поддерживает `line.style_solid`, `line.style_dotted` и
`line.style_dashed`. По умолчанию она сплошная и имеет ширину
1 пиксель. OpenPine допускает ширину от 1 до 64 пикселей.
Ошибочные значения получают `BOX_BORDER_WIDTH`,
`BOX_BORDER_STYLE` или `BOX_EXTEND`.
[Справка Pine](https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/#box-styles)

`extend.left`, `extend.right` и `extend.both` продлевают
горизонтальные стороны. Заливка и вертикальные стороны привязаны
к исходным углам. API включает бокс в окно, если туда попадает
продлённая видимая сторона. График обрезает её на краю окна.
Вертикальный бокс с совпадающими X остаётся вертикальной рамкой.
Прозрачный цвет рамки скрывает её вместе с продлением.
[Правила продления в Pine v4](https://www.tradingview.com/pine-script-docs/v4/essential/drawings/#modifying-drawings)

Сеттеры обновляют общий объект по ID. Исходные углы, ширина, стиль
и режим продления сохраняются в журнале и checkpoint. Старые
записи читаются с настройками рамки по умолчанию. Предпросмотр
открытой свечи показывает свой вариант оформления до закрытия.
[Сценарии conformance](../conformance/box-borders/cases.json)
