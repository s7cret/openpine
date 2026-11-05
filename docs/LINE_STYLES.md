# Оформление линий Pine

Координатный `line.new` и функции `line.set_color`, `line.set_style`,
`line.set_width` доступны в Pine v4–v6. В v5–v6 параметры оформления
принимает и перегрузка `line.new(first_point, second_point, ...)`.
В v1–v3 вызовы получают `LINE_VERSION`. [Профиль сигнатур](../spec/line-styles-profile.json)

```pine
//@version=6
indicator("Line appearance", overlay=true)
var line level = line.new(0, open, 1, open, extend=extend.right, color=color.blue, style=line.style_dashed, width=2)
line.set_color(level, close > open ? color.green : color.red)
line.set_width(level, close > open ? 3 : 1)
```

Стиль может быть сплошным, штриховым, точечным или со стрелкой на
левом, правом либо обоих концах. Оформление по умолчанию использует
`color.blue`, `line.style_solid` и ширину в 1 пиксель. В OpenPine
допустима целая ширина от 1 до 64. Для других значений действует
`LINE_WIDTH`, для неизвестного стиля действует `LINE_STYLE`.
[Справка TradingView](https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/#line-styles)

`color=na` и полностью прозрачный цвет скрывают обводку. Геометрия
доступна заливке `linefill` и вычислению `line.get_price` для
линий в режиме `xloc.bar_index`. Canvas рисует стрелки у видимых
исходных концов линии. На вертикали первый и второй конец
соответствуют левому и правому стилю. [Проекция](DRAWING_XLOC.md)

Сеттеры меняют общий объект по ID. Событие `update` и checkpoint
сохраняют цвет, стиль и ширину вместе с исходными координатами.
Для старой записи первый сеттер дополняет недостающие поля значениями
по умолчанию. Replay пересчитывает оформление при закрытии свечи.
[Сценарии conformance](../conformance/line-styles/cases.json)

Внутренние проверки подтверждают профиль OpenPine. Сверка визуальных
деталей с TradingView проводится отдельно.
