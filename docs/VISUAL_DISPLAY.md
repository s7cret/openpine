# Видимость визуальных рядов

`display.all` показывает слой, `display.none` скрывает его на графике.
Оба значения доступны через именованный аргумент `display`.

| Вызовы | Pine v1–v3 | Pine v4 | Pine v5–v6 |
| --- | --- | --- | --- |
| `plot`, `plotshape`, `plotchar`, `plotarrow`, `plotbar`, `plotcandle` | — | `all` / `none` | `all` / `none` |
| `hline`, `bgcolor`, `barcolor` | — | — | `all` / `none` |
| Обычный `fill` и градиентный `fill` между `plot` | — | — | `all` / `none` |

Профиль принимает константу или условный выбор режима через `bool`-литерал
либо объявленный глобально булев input. Выбор фиксируется на запуске.
`display.none` скрывает рисунок, подпись и подсказку. Скрытый ряд
самостоятельно не расширяет шкалу панели. Значения ряда доступны
через API диапазона и в полном экспорте.

```pine
//@version=6
indicator("Канал")
show = input.bool(false, "Показывать границы")
hi = plot(high, display=show ? display.all : display.none)
lo = plot(low, display=show ? display.all : display.none)
fill(hi, lo, high, low, color.new(color.teal, 25), na)
```

Обычный и градиентный `fill` используют значения скрытых опор.
Видимость заливки определяется её собственными параметрами.
Если панель состоит из видимой заливки со скрытыми опорами,
для шкалы используются её видимые сегменты. При наличии видимых
рядов диапазон задают они. [ADR 0033](adr/0033-visual-display.md)
Окна `show_last`, смещения `offset` и размещение `force_overlay` задают
геометрию, которую используют исторический график и replay.

Сценарии с фиксированными ожиданиями находятся в
[`visual-bands`](../conformance/visual-bands/cases.json),
[`visual-signals`](../conformance/visual-signals/cases.json) и
[`visual-ohlc`](../conformance/visual-ohlc/cases.json).
Восстановление видимости, экспорт и replay проверяет
[`test_visual_display.py`](../tests/test_visual_display.py).

[Визуальные режимы TradingView](https://www.tradingview.com/pine-script-docs/visuals/overview/#display-in-other-locations) · [История изменений Pine](https://www.tradingview.com/pine-script-docs/release-notes/)
