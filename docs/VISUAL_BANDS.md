# Цветовые настройки, уровни и заливки

`openpine.visual-bands.1` — ограниченный профиль поверх `openpine.colors.1`.
Существующие импортированные bundle, input/color profiles и числовые результаты
сохранены. Правила нового профиля закреплены в `spec/visual-bands-profile.json`.

| Возможность | v1–v3 | v4 | v5/v6 |
| --- | --- | --- | --- |
| Цветовой input | Явный отказ | `input(..., type=input.color)` | `input(...)` / `input.color(...)` |
| `nz(color)` | Да; v1/v2 — реконструкция | Да | Да |
| Глобальные ссылки `p=plot(...)`, `h=hline(...)` | Да | Да | Да |
| `fill(plot,plot,color)` / `fill(hline,hline,color)` | Да | Да | Да |
| Именованный `fillgaps` | Недоступен; default `false` реконструирован из v4 | const bool, default false | const bool, default false |
| Именованный `force_overlay` для `plot` | Недоступен | Недоступен | const bool, default false |
| Именованный `transp` | Да начиная с v3 | Да | v5 да, v6 недоступен |

Ранние версии используют уже зафиксированную цветовую палитру/reconstruction.
Доступность color input проверена отдельно: TradingView объявил её для v4
[17 сентября 2020](https://www.tradingview.com/blog/en/create-color-inputs-in-pine-20751/);
в [input v3](https://www.tradingview.com/pine-script-docs/v3/annotations/script-inputs/)
color отсутствует. Это граница версии, а не перенос поведения v6 назад.

Цветовой input принимает const HEX, палитру, предыдущую const color binding или
ограниченный чистый конструктор цвета глубиной до 16. `na`, series/input defaults,
HEX-looking string, options и числовые constraints отклоняются. Overrides и
пресеты канонизируются в `#RRGGBBAA`. В UI доступны HEX, выбор RGB и непрозрачности.
Настройки закреплены при запуске: последующее редактирование пресета не меняет
приостановленное задание. `nz(color)` без replacement даёт `#00000000` согласно
[type system](https://www.tradingview.com/pine-script-docs/language/type-system/).

```pine
//@version=6
indicator("Channel")
c = input.color(#2196F340)
m = ta.sma(close, 2)
lower = plot(m - 2, "Lower")
upper = plot(m + 2, "Upper")
fill(lower, upper, c, fillgaps=false)
```

Ссылки имеют закрытые типы `plot_id` / `hline_id`, const qualifier и стабильный
ID письменного callsite. Разрешены прямые глобальные bar declarations и aliases
уже объявленных ссылок. Var/varip, reassignment, history, arithmetic, UDF,
контейнеры и вложенный `fill(plot(...),...)` вне профиля. Это не drawing heap.
Обычный standalone `plot` сохраняет прежний ABI. В v5–v6
`force_overlay=true` переносит на ценовую панель и обычный ряд, и ряд
со ссылкой. Заливка видна на общей панели двух границ. При разных
панелях она остаётся в результате и график её пропускает.
[Правила размещения](DRAWING_OVERLAY.md)

`hline` принимает const/input цену, цвет, стиль и ширину; series запрещены.
В v3 цвет ограничен const по закреплённому справочнику.
Стили: solid/dotted/dashed, ширина 1–64. До v4 константы bare,
начиная с v4 — `hline.style_*`. Отсутствующая цена/цвет скрывает линию.
Произвольные числовые style codes старых версий вне этого закрытого профиля.
Справочник не задаёт default цвета/стиля hline: профиль явно выбирает
`#2196F3FF` / solid из уже принятого plot-профиля. Это выбор OpenPine,
его внешнее совпадение не установлено. Аналогично закреплены пустые title
и legacy fill transp=90 при отсутствии документированного default.
Стиль — const/input metadata в глобальной bar binding; persistent/history,
UDF results и контейнеры стилей не допускаются, включая подменённый saved IR.
Смешанные plot/hline endpoints отклоняются, как требует
[справка fill](https://www.tradingview.com/pine-script-docs/visuals/fills/).
Сигнатура fill ограничена первыми тремя positional arguments; title, fillgaps,
transp передаются по имени. Для pre-v6 default transp=90 применяется к opaque
цвету; существующая альфа цвета сохраняется. Цвет может меняться на каждом баре.

Typed IR `.22` и runtime state `.14` включаются только при использовании нового
профиля. Output metadata `openpine.visual-layer.1` хранит kind, references,
fillgaps, width/style. Уровень использует существующий поток числовых рядов,
заливка — null scalar и отдельный цвет. Большие ряды идут в прежний Parquet;
range API возвращает descriptor и нужное окно values/colors. Renderer рисует
заливки в source order под линиями; fillgaps управляет соединением отсутствующих
значений endpoints. Скрытие линии в UI сохраняет её данные для видимой заливки.
Checkpoint и realtime preview проверяют принадлежность ссылок программе.

Примеры: `examples/corpus/bands-routing-v1..v6.pine`,
`bands-rsi-v6.pine`, `bands-ma-channel-v6.pine`. Независимые ожидания —
`conformance/visual-bands/`; проверки восстановления на каждом event cut —
`tests/test_visual_bands.py`; browser canvas/picker — `tests/ui/bands.html`.
Installed helper `bands` в numeric suite проверяет CLI/source/saved IR, HTTP,
worker pause/resume, Parquet ranges и замороженный цвет после изменения пресета.

Gradient fill overloads, editable/display, drawing objects и точное внешнее
сопоставление с TradingView остаются отдельным этапом. Внутренние fixtures
проверяют описанную модель, а не полную совместимость каждой версии Pine.
