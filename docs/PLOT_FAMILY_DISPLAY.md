# Места вывода меток, стрелок и OHLC

В Pine v5–v6 параметр `display` принимает константы, комбинации
через `+` и `-` и условный выбор через булев input. Значение
фиксируется при запуске. В v4 доступны `all` и `none` для семейства
`plot*`. В v1–v3 параметр недоступен.
[История изменений Pine](https://www.tradingview.com/pine-script-docs/release-notes/#july-2022)
· [Числовой plot](PLOT_DESTINATIONS.md)

| Функции | Панель | Строка статуса и окно данных | Шкала |
| --- | --- | --- | --- |
| `plotshape`, `plotchar`, `plotarrow` | Метка, символ или стрелка | Одно значение на свечу | Флаг `price_scale` допустим в выражении. Эти функции не создают метку шкалы. |
| `plotbar`, `plotcandle` | OHLC-слой | Четыре значения O/H/L/C | Последний `close` слоя |

```pine
//@version=6
indicator("Метки и свечи")
plotshape(close > open, "Рост", display=display.pane + display.data_window)
plotcandle(open, high, low, close, "OHLC", force_overlay=true,
           display=display.status_line + display.price_scale)
```

При расширенном `display` у `plotshape` и `plotchar` хранятся исходные
bool/int/float. В числовых полях `true` даёт 1, `false` даёт 0,
а `na` остаётся пропуском. Для `false` рисунок отсутствует. Числовой
ноль рисуется в `location.absolute`. Прежняя двоичная перегрузка
сохраняет исходный формат результата, в котором скрытый образец
мог храниться как пропуск. Для вывода всех полей с исходными значениями
можно использовать `display.all - display.none`.

Статус и окно данных используют исходную свечу с учётом `show_last`.
`offset` сдвигает геометрию меток и стрелок. `force_overlay` выбирает
ценовую панель. Если `pane` исключён, слой выпадает из автошкалы.
OHLC-слой остаётся источником значений в выбранных местах вывода.

Метка OHLC на шкале использует `close` последней подтверждённой
свечи, включая просмотр ранней страницы. При следовании в replay
значение приходит из действующего превью открытой свечи. Пропуск
убирает метку. Parquet и полный экспорт сохраняют полный ряд.
[ADR 0035](adr/0035-plot-family-display.md)

Поведение проверяется в корпусах
[visual-signals](../conformance/visual-signals/cases.json) и
[visual-ohlc](../conformance/visual-ohlc/cases.json).
Внешняя сверка с TradingView проводится отдельно.
