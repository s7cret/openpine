# Временные координаты рисунков Pine v4–v6

Индикаторы принимают именованный `xloc` в координатных конструкторах
`line.new`, `label.new` и `box.new`. В режиме `xloc.bar_time` целое X
означает Unix-время в миллисекундах. В режиме `xloc.bar_index` X
означает номер бара. Этот режим применяется и к прежним записям
без поля `xloc`. Существующие setters используют режим объекта.
[Закреплённые сигнатуры](../spec/drawing-xloc-profile.json)

Режим существующего рисунка можно сменить вместе с координатами X.
Геттеры возвращают координаты в текущем режиме.
[Смена режима и чтение X](DRAWING_XLOC_MUTATIONS.md)

```pine
//@version=6
indicator("Timed drawings", overlay=true)
var line roof = line.new(time, high, time + 60000, high, xloc=xloc.bar_time, extend=extend.right)
var label tag = label.new(time, close, "time", xloc=xloc.bar_time)
var box zone = box.new(time, high, time + 60000, low, xloc=xloc.bar_time, bgcolor=#33669980)
if bar_index > 0
    line.set_xy2(roof, time, high)
    label.set_xy(tag, time, close)
    box.set_rightbottom(zone, time, low)
```

`line.get_price` принимает линии с `xloc.bar_index`. Для живой линии
по времени OpenPine выдаёт `LINE_PRICE_XLOC`. В Pine v1–v3 остаются
версионные диагностики `LINE_VERSION`, `LABEL_VERSION` и `BOX_VERSION`.
[Справка TradingView](https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/)

## Проекция OpenPine

Точное совпадение времени ставит рисунок на свечу. Между двумя
наблюдаемыми свечами позиция вычисляется линейно, в том числе
внутри рыночного разрыва. За краями снимка используется интервал
ближайшей пары свечей. В снимке из одной свечи доступна только её
точная позиция.

Проекция опирается на глобальные индексы закреплённого снимка или
подтверждённого префикса replay. Смена страницы графика сохраняет
позицию. Линии, зоны и метки по времени получают поля `chart_*`
в ответе окна. `linefill` использует общую ось для обеих границ,
в том числе когда их режимы различаются.

Результат, checkpoint и события хранят исходные координаты.
Предпросмотр replay использует дополнительно время открытой
свечи. Индекс SQLite для закрытий первичного контекста хранит
время и порядковый номер. Запросы подтверждённого графика читают
индекс до опубликованного бара.

Этот профиль вводит схему каталога 17. Существующий каталог
обновляется через команду с резервной копией.
[Обновление хранилища](OPERATIONS.md)

Сверка поведения с TradingView проводится отдельно.
[Внутренние сценарии](../conformance/drawing-xloc/cases.json)
