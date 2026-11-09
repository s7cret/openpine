# Порядок визуальных слоёв

`explicit_plot_zorder` доступен у `study` и `strategy` в Pine v4, а также
у `indicator` и `strategy` в v5–v6. При `true` более поздний вызов
`plot*()`, `hline()` или `fill()` рисуется поверх более раннего.
По умолчанию заливки рисуются под рядами, а горизонтальные уровни поверх
рядов. Внутри группы действует порядок вызовов. Фон, рисуемые объекты
и таблицы имеют свои группы. [Справка Pine](https://www.tradingview.com/pine-script-docs/language/declaration-statements/#explicit_plot_zorder)

`behind_chart` доступен у `indicator` и `strategy` в v5–v6. При
`overlay=true` обычные визуальные слои скрипта рисуются за рыночным
рядом по умолчанию. `behind_chart=false` переносит их перед рядом.
`bgcolor` задаёт фон панели, `barcolor` окрашивает рыночные бары.
[Правила размещения](https://www.tradingview.com/pine-script-docs/language/declaration-statements/#overlay-scale-and-behind_chart)

OpenPine размещает принудительно наложенные ряды и рисунки перед рыночным
рядом. Пример с независимой шкалой и двумя линиями находится в
[сценарии Pine v6](../conformance/visual-bands/v6-visual-order-forced.pine).
Расположение относительно свечей для сочетания настроек проверяется
в браузерном сценарии OpenPine. Сверка изображения TradingView остаётся
отдельной проверкой. [ADR 0038](adr/0038-visual-order.md)

История и replay читают `visual_order` из результата, в том числе при
показе предварительного бара. Если старый результат не содержит это поле,
действует прежняя последовательность OpenPine. Параметры декларации в
текущем профиле передаются по имени.
