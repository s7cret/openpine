# Ломаные и полигоны Pine v5–v6

Индикатор строит `polyline` из `array<chart.point>`. В этом профиле
поддерживаются прямые сегменты, замыкание контура, цвет линии и
заливка. В v1–v4 тип и вызовы `polyline` выдают `POLYLINE_VERSION`.
[Справка Pine](https://www.tradingview.com/pine-script-docs/v5/concepts/lines-and-boxes/#polylines)

```pine
//@version=6
indicator("Point path", overlay=true)
var array<chart.point> vertices = array.new<chart.point>()
var polyline path = na
if bar_index < 3
    array.push(vertices, chart.point.now())
if bar_index == 2
    path := polyline.new(vertices, closed=true, xloc=xloc.bar_time,
                         line_color=#336699, fill_color=#33669940)
```

Массив сохраняет ссылки. `array.get` возвращает ту же точку, поэтому
запись в `price` видна через алиас. Доступны `array.new<chart.point>`,
`array.from`, `array.push`, `array.get`, `array.set`, `array.clear`,
`array.size` и `na`.

`polyline.new` копирует координаты по порядку массива. `xloc` выбирает
`index` или `time`, по умолчанию `index`. Значение `closed=true`
соединяет последнюю вершину с первой. `line_color=na` скрывает обводку.
Заливка применяется к замкнутому контуру. Для смены формы скрипт
вызывает `polyline.delete` и создаёт новый рисунок.

В OpenPine открытый контур принимает от 2 до 512 вершин, замкнутый
от 3 до 512. Для каждой точки нужны выбранный X и конечная цена.
Одновременно допускается 50 рисунков и 8 192 вершины. Суммарное
число вершин созданных контуров ограничено 8 192 за бар.
При заполнении счётчика рисунков вытесняется самый старый.

Результат и журнал содержат исходные вершины. Для времени
API окна добавляет `chart_vertices` на общей оси свечей. График
обрезает обводку и заливку по видимой области и использует
видимые цены при вычислении масштаба.
[Проекция времени](DRAWING_XLOC.md)

Checkpoint хранит исходный массив в общем heap и снимки контуров
в состоянии рисунков. Журналы jobs и replay доступны через
`polyline_events` с размером страницы до 100 событий.
Предпросмотр открытого бара публикует отдельную геометрию.
Сверка исполнения с TradingView проводится отдельно.
[Профиль](../spec/polylines-profile.json) · [Conformance](../conformance/polylines/cases.json)
