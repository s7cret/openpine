# Точки графика Pine v5–v6

`chart.point` хранит время в миллисекундах, индекс бара и цену. Обычная
переменная и её алиас ссылаются на одну точку. Поля `time`, `index` и
`price` доступны для чтения, `price` можно менять. `chart.point.copy(p)`
и `p.copy()` создают отдельную точку с теми же значениями.

| Вызов | Поля точки |
| --- | --- |
| `chart.point.new(time, index, price)` | Все три поля задаёт скрипт |
| `chart.point.now(price)` | Время и индекс текущего бара, цена по умолчанию `close` |
| `chart.point.from_index(index, price)` | Индекс и цена, `time = na` |
| `chart.point.from_time(time, price)` | Время и цена, `index = na` |

Конструкторы `line.new`, `label.new` и `box.new` принимают точки.
`xloc` выбирает поле для X, по умолчанию используется `index`. Для
`xloc.bar_time` выбирается `time`. При отсутствии выбранного поля рисунок
сохраняет `na` и скрыт до установки координаты. Передача отсутствующей
ссылки при создании рисунка выдаёт `UDT_NA`.

```pine
//@version=6
indicator("Point snapshots", overlay=true)
var chart.point start = chart.point.now()
var chart.point finish = chart.point.from_index(bar_index + 2, close)
var line first = line.new(start, finish)
alias = start
alias.price := close + 5
var line second = line.new(start, finish)
var label tag = label.new(start, "Point", xloc=xloc.bar_time)
label.set_point(tag, chart.point.now(high))
```

Конструктор или сеттер копирует координаты при вызове. Изменение
`start.price` через алиас влияет на последующие вызовы. Сеттеры
`line.set_first_point`, `line.set_second_point`, `label.set_point`,
`box.set_top_left_point` и `box.set_bottom_right_point` используют
`xloc` существующего рисунка. Оформление ограничено уже принятыми
параметрами `extend`, `text`, `border_color` и `bgcolor`.
[Справка Pine v5](https://www.tradingview.com/pine-script-docs/v5/concepts/lines-and-boxes/)

Рисунки проходят через существующие журналы и общую ось графика.
Checkpoint сохраняет ссылки точек в общем heap. При replay изменение
цены в открытом баре видно в preview, подтверждённое состояние хранится
отдельно. Форматы программы и checkpoint для точек имеют версии
Typed IR .30 и runtime-state .22.

В Pine v1–v4 тип и функции `chart.point` получают `POINT_VERSION`.
Внешняя сверка поведения с TradingView проводится отдельно.
[Профиль](../spec/chart-points-profile.json) · [Conformance](../conformance/chart-points/cases.json)
