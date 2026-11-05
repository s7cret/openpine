# Смена координатного режима рисунков

| Операция | Pine v1–v3 | Pine v4–v5 | Pine v6 |
| --- | --- | --- | --- |
| `line.set_xloc(id, x1, x2, xloc)` | `LINE_VERSION` | Да | Да |
| `label.set_xloc(id, x, xloc)` | `LABEL_VERSION` | Да | Да |
| `box.set_xloc(id, left, right, xloc)` | `BOX_VERSION` | `BOX_SET_XLOC_VERSION` | Да |
| `line.get_x1(id)`, `line.get_x2(id)` | `LINE_VERSION` | Да | Да |
| `label.get_x(id)` | `LABEL_VERSION` | Да | Да |
| `box.get_left(id)`, `box.get_right(id)` | `BOX_VERSION` | Да | Да |

Параметр `xloc` принимает константу `xloc.bar_index` или
`xloc.bar_time` непосредственно в вызове. В режиме `bar_index`
переданные X обозначают индексы, в режиме `bar_time` обозначают
Unix-время в миллисекундах. Setter сразу записывает режим и все переданные
X. ID, координаты Y, текст, цвета, продолжение линии и связи `linefill`
сохраняются. Переменные с тем же ID сразу видят обновление.
[Закреплённые сигнатуры](../spec/drawing-xloc-mutations-profile.json)

```pine
//@version=6
indicator("Switch xloc", overlay=true)
var line roof = line.new(0, 25, 3, 40)
if bar_index == 1
    line.set_xloc(roof, time, time + 120000, xloc.bar_time)
if bar_index == 3
    line.set_xloc(roof, -1, bar_index, xloc.bar_index)
plot(line.get_x1(roof), "Raw X")
```

Геттеры возвращают исходное знаковое 64-битное целое как `series int`.
Геттер возвращает `na` для отсутствующего ID, удалённого рисунка или
отсутствующего X. Setter с отсутствующим или удалённым ID пропускает
обновление. Для живой линии по времени `line.get_price` выдаёт
`LINE_PRICE_XLOC`. После возврата к индексам функция использует
обновлённые точки линии.

В журнал записывается одно событие `update` на изменение живого
рисунка. Сохранённый IR хранит аргументы вызова. Checkpoint и журнал
сохраняют вычисленные X в исходных единицах. График вычисляет экранную
позицию из времени отдельно.
В replay предпросмотр открытой свечи использует отдельное состояние.
Закрытие свечи пересчитывает обновление на подтверждённых данных.
[Проекция времени](DRAWING_XLOC.md) · [Сценарии conformance](../conformance/drawing-xloc-mutations/cases.json)

Этот профиль покрывает вызовы в индикаторах OpenPine. Сверка поведения
с TradingView ведётся отдельно. [Справка TradingView](https://www.tradingview.com/pine-script-docs/visuals/lines-and-boxes/) ·
[Появление `box.set_xloc` в Pine v6](https://www.tradingview.com/pine-script-docs/release-notes/#march-2025)
