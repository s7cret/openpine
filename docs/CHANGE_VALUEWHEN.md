# `change()` и цвет события

В Pine v1–v4 используется `change()`, в v5–v6 — `ta.change()`.
В OpenPine вызов с одним аргументом даёт тот же результат, что и
явная длина 1. Для `int` и `float` это разность, для `bool` — факт
изменения. Пропуск в числовом ряду возвращает `na`. При недостающей
истории `bool` даёт `na` в v1–v5 и `false` в v6. Динамическая длина
поддерживается историческим профилем OpenPine в v1–v6. TradingView
задокументировал её появление в v4.
[Изменяемая длина в Pine](https://www.tradingview.com/blog/en/pine-functions-support-dynamic-length-arguments-20554/) ·
[Правила `bool` в v6](https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/#boolean-values-cannot-be-na).

В Pine v5–v6 `ta.valuewhen(condition, source, occurrence)` принимает
`series color`. `occurrence=0` выбирает последний `true`, `1` — предыдущий.
Когда `condition` равен `true`, цвет `na` тоже занимает позицию события.
До нужного числа срабатываний возвращается `na`. Источник `color(na)`
выбирает цветовую форму, а нетипизированный `na` — числовую.
[Справочник Pine](https://www.tradingview.com/pine-script-reference/v6/#fun_ta.valuewhen).

[Пример](../examples/change-valuewhen/direction-color.pine) запрашивает
`ta.change(close)` на двухминутных свечах. `ta.valuewhen()` сохраняет цвет
последней смены направления. После восстановления replay сохраняются
значения, прозрачность и журнал оповещений. При подтверждённом переходе
с минуса на плюс появляется одно оповещение.
[Решение о сигнатурах и хранении](adr/0064-change-valuewhen.md).
