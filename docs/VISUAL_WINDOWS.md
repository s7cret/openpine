# Окна визуальных рядов

| Вызов | Pine v1–v2 | Pine v3 | Pine v4 | Pine v5–v6 |
| --- | --- | --- | --- | --- |
| `plot`, `bgcolor`, `barcolor`, `plotbar`, `plotcandle` с `show_last` | — | const int | const/input int | const/input int |
| `plot` с именованным `offset` | — | const/input int | const/input int | const/input int |
| `fill(plot, plot, color)` с `show_last` | — | const int | const/input int | const/input int |

Размер окна задаётся именованным аргументом. Ноль скрывает слой, а
`na` и пропущенное значение показывают весь рассчитанный диапазон.
Для сдвига `plot` принимаем целые от −10 000 до 10 000. Положительное
значение сдвигает точки вправо. Окно отсчитывается от конца расчёта,
а затем смещение меняет координаты допущенных точек. При replay
предпросмотр видимого открытого бара временно сдвигает границу окна.
В v1–v2 статический `offset` доступен в архивной позиционной форме.
[Оформление plot](PLOT_APPEARANCE.md)

```pine
//@version=6
indicator("Окно канала", overlay=true)
a = plot(close + 2, offset=1, show_last=20)
b = plot(close - 2, offset=-1)
fill(a, b, color.new(color.blue, 85), show_last=20, fillgaps=false)
```

Заливка соединяет допущенные значения границ в общих координатах.
Её цвет и окно привязаны к индексу этой координаты. `fillgaps=true`
может соединить точки через пропуск внутри окна, если видны обе
границы по его сторонам. Сегмент начинается на первом допущенном
баре или позже. Для окна из одного бара у заливки нет площади.
Скрытые вычисленные OHLC не расширяют шкалу. Рыночные свечи
сохраняют свой цвет там, где слой `barcolor` их не перекрашивает.

Parquet и полный экспорт сохраняют исходные ряды. API окна возвращает
смещённые точки из соседнего диапазона в `halo` с исходным индексом
относительно начала страницы. Для истории и replay применяется одна
проекция. Сохранённые программы используют отдельные ABI только при
явной передаче новых аргументов.

[Решение по v3–v4](adr/0030-visual-output-windows.md) · [Справка по plot](https://www.tradingview.com/pine-script-docs/visuals/plots/#plot-parameters) · [Справка по fill](https://www.tradingview.com/pine-script-docs/visuals/fills/#plot-and-hline-fills)
