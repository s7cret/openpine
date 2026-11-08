# Стили plot и опорные уровни

| Возможность | Версии Pine | Форма вызова |
| --- | --- | --- |
| `histbase`, `trackprice`, `join` | v1–v6 | Позиционная в v1–v2, именованная в v3–v6 |
| `areabr` | v1–v6 | Bare-имя до v4, `plot.style_areabr` с v4 |
| `plot.style_steplinebr`, `plot.style_stepline_diamond` | v5–v6 | Аргумент `style` |
| `plot.linestyle_solid`, `plot.linestyle_dashed`, `plot.linestyle_dotted` | v6 | Именованный аргумент `linestyle` |

`histbase` принимает конечный const/input int или float. Гистограмма,
столбцы и области рисуются относительно этой базы. Для `histogram`,
`columns` и `area` база входит в шкалу, когда виден ряд с окном.
`areabr` разрывает заливку на `na` и строит шкалу по значениям ряда.
`steplinebr` разрывает ступенчатую линию на `na`, а `stepline_diamond`
отмечает новое значение ромбом в точке изменения. API страницы передаёт
один соседний отсчёт для ромба на её первой видимой точке.

`join=true` соединяет соседние точки `circles` и `cross` линией в
один пиксель. Пропуск разрывает соединение. В v6 `linestyle` задаёт
штрих для `line`, `linebr`, `stepline`, `stepline_diamond` и контура
`area`. Параметр принимает закрытый тип `plot_line_style` с
квалификатором const или input.

`trackprice=true` рисует пунктир по значению последнего бара расчёта.
Цвет берётся с того же бара. Завершающий `na`, невидимый цвет и
`show_last=0` скрывают линию. Координата уровня берётся из полного
результата и одинакова на страницах с разным смещением. Предпросмотр
открытого бара временно показывает его уровень. Подтверждённые
значение и цвет сохраняются в дескрипторе результата.

```pine
//@version=6
indicator("Отклонение от опорного уровня")
base = input.float(100, "База")
plot(close, style=plot.style_histogram, histbase=base, show_last=30, trackprice=true)
plot(base, linestyle=plot.linestyle_dashed)
```

Parquet хранит исходные значения и цвета. Полный экспорт включает
параметры оформления и все отсчёты. Сравнение отрисовки с TradingView
проводится отдельно.
[ADR 0031](adr/0031-plot-appearance.md) · [Справочник v5](https://www.tradingview.com/pine-script-reference/v5/#fun_plot) · [Справка по стилям](https://www.tradingview.com/pine-script-docs/visuals/plots/#plot-parameters)
