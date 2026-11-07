# Рисунки на ценовой панели

В Pine v5–v6 `label.new`, `box.new` и `line.new` принимают `force_overlay`
в координатном и точечном вариантах. `polyline.new` и `table.new`
принимают этот параметр по имени. Параметр имеет тип `const bool`.
В Pine v1–v4 вызов получает диагностику версии.
[Обновление Pine v5](https://www.tradingview.com/pine-script-docs/v5/release-notes/#june-2024)

```pine
//@version=6
indicator("Two panes", overlay=false)
plot(close - open, "Change")
var label marker = label.new(bar_index, na, "Price", yloc=yloc.abovebar,
     tooltip="Main chart", force_overlay=true)
var box zone = box.new(bar_index, high, bar_index + 1, low, text="Zone", force_overlay=true)
var line level = line.new(bar_index, high, bar_index + 1, high, force_overlay=true)
label.set_xy(marker, bar_index, na)
box.set_lefttop(zone, bar_index, high)
box.set_rightbottom(zone, bar_index + 1, low)
```

При `force_overlay=true` объект попадает на ценовую панель.
При `false` или пропущенном параметре он наследует `overlay`
индикатора. Значение `overlay=true` размещает все эти объекты
на ценовой панели. Метка `yloc.abovebar` на этой панели
привязана к максимуму свечи. Числовые координаты Y используют
шкалу выбранной панели.
[Правило размещения Pine](https://www.tradingview.com/pine-script-docs/visuals/overview/#overlay)

Флаг хранится в результате и журнале вместе с объектом.
Сеттеры сохраняют его, а восстановление и live используют
его в подтверждённом состоянии и предпросмотре.
[ADR 0025](adr/0025-drawing-pane-override.md)

Обводка и заливка замкнутой полилинии используют одну панель.
В OpenPine `linefill` отображается, когда обе границы попали на одну
панель. Если границы на разных панелях, заливка сохраняет ID,
цвет и ссылки на обе линии и остаётся доступной скрипту.
[ADR 0026](adr/0026-linefill-pane-routing.md)
