# Вычисляемые свечи и бары

В OpenPine `plotcandle()` рисует свечи, а `plotbar()` — классические
OHLC-бары. Вызов принимает четыре числовых ряда, необязательные заголовок
и цвет. В v1–v2 базовая сигнатура допускает до шести позиционных аргументов.
Для v3–v6 `plotcandle` также принимает `wickcolor` и `bordercolor`.
Параметры после `wickcolor` передаются по имени. В v5–v6 обе функции
принимают именованный `force_overlay`.
[Справка Pine](https://www.tradingview.com/pine-script-docs/visuals/bar-plotting/)

```pine
//@version=6
indicator("Calculated OHLC")
plotcandle(open+100, high+100, low+100, close>open ? close+100 : na,
           "Shifted candles", color=#2196F380, wickcolor=#2196F3, bordercolor=na)
plotbar(open, high, low, close, "OHLC", color=#663399, force_overlay=true)
```

Если хотя бы один OHLC-аргумент равен `na`, в слое остаётся пропуск
на индексе этого бара. Верхняя и нижняя границы нормализуются по максимуму
и минимуму четырёх аргументов. Цвет по умолчанию `#2196F3FF`, заголовок
по умолчанию пуст. Значение `na` в отдельном цветовом аргументе скрывает
соответствующую часть фигуры. Вызовы размещаются в глобальной области.

Каждый вызов формирует слой `openpine.ohlc-layer.1` со стабильным ID,
привязкой к исходным барам и собственной панелью. Цвета тела, тени и рамки
хранятся вместе с OHLC в записи бара. Исторический API окна и экспорт
возвращают `bars` внутри слоя, replay публикует `ohlc_rows` и образец
в `preview.outputs[].presentation.bar`. Позиции скалярного `values`
зарезервированы как `null`. Слой участвует в шкале своей панели и
рисуется после рыночной серии. Воспроизведение и исторический график
используют одну реализацию отрисовки.

`editable`, `show_last`, `display`, `format` и `precision` остаются
за пределами этого профиля. В матрице обе перегрузки отмечены `PARTIAL`.
Сверка исполнения с TradingView проводится отдельно.
[Профиль](../spec/visual-ohlc-profile.json) · [Сценарии](../conformance/visual-ohlc/cases.json) · [ADR 0028](adr/0028-visual-ohlc-layers.md)
