# Формат чисел на графике

| Pine | Декларация скрипта | Аргументы отдельного ряда |
| --- | --- | --- |
| v1–v2 | `study`: `precision` | |
| v3 | `study`, `strategy`: `precision` | |
| v4 | `study`, `strategy`: `format` и `precision` | |
| v5–v6 | `indicator`, `strategy`: `format` и `precision` | `plot`, `plotshape`, `plotchar`, `plotarrow`, `plotbar`, `plotcandle` |

Декларация принимает литералы, а в v5–v6 параметр отдельного ряда
также может ссылаться на прямой глобальный `input.int` или `input.string`.
Выбранное значение фиксируется при запуске. Формат и точность
наследуются и переопределяются независимо. Допустимая точность
в OpenPine составляет 0–16 знаков. Для `strategy` дополнительные
параметры передаются по имени.
[История Pine](https://www.tradingview.com/pine-script-docs/release-notes/#december-2023)

```pine
//@version=6
indicator("Формат чисел", format=format.percent, precision=4)
places = input.int(2)
plot(close, "Цена", format=format.price, precision=places)
plot(close - open, "Разница", display=display.status_line + display.data_window)
```

- `format.price` использует заданную точность или два знака после запятой.
- `format.percent` добавляет знак `%` к исходному числу. Точность
  задаётся так же, как для цены.
- `format.volume` округляет по величине числа и сокращает крупные числа
  суффиксами K/M/B/T. У сокращённого числа до двух знаков после запятой.
- `format.inherit` доступен в декларации. Явная точность задаёт формат
  цены. Иначе OpenPine берёт количество знаков из закреплённого
  минимального шага цены инструмента, а при его отсутствии использует
  текущий формат интерфейса.

Метки на шкале, строка статуса и окно данных используют настройки
своего ряда. Деления отдельной панели используют настройки декларации.
Для O/H/L/C действует один формат. Значение `na` остаётся пропуском.
В истории и replay применяются одинаковые правила.
[Правила TradingView](https://www.tradingview.com/pine-script-docs/language/declaration-statements/#format-and-precision)
· [Места вывода](PLOT_FAMILY_DISPLAY.md)

Декларация результата содержит формат скрипта и настройки рядов по ID.
Parquet и экспорт сохраняют исходные числа. Сохранённые результаты без
метаданных используют прежний формат OpenPine.
[ADR 0036](adr/0036-number-format.md)
