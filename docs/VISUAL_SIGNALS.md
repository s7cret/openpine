# Раскраска и сигнальные метки

`openpine.visual-signals.1` добавляет `bgcolor`, `barcolor`, `plotshape`,
`plotchar` и `plotarrow` для Pine v1–v6. Правила закреплены в
`spec/visual-signals-profile.json` с исходными entry ID, basis, syntax,
типами и default claims из неизменяемого Wiki snapshot. V1 опирается на
primary archive; v2 — compatibility baseline/reconstruction; v3–v6 — primary.
Внутреннее соответствие этому профилю не устанавливает точного совпадения
с выполнением TradingView.

| Параметр | v1/v2 | v3/v4 | v5 | v6 |
| --- | --- | --- | --- | --- |
| Пять функций | Да | Да | Да | Да |
| Именованные аргументы | Вне исторически проверенного профиля | Да | Да | Да |
| `transp` для bgcolor/меток | В legacy positional order | В legacy order | Только именованный | Недоступен |
| `size` для меток | Нет; внутренний auto | const, именованный | const, именованный | const, именованный |
| `offset` | const/input, ограниченное подмножество старого series offset | То же | То же | const/input, подмножество simple offset |
| `show_last` для всех пяти функций | Недоступен | v3 const, v4 const/input | const/input | const/input |

Все вызовы — непосредственные глобальные statements. Функции не создают
ссылок на изменяемые drawing objects и не используются как значения,
в UDF, if или циклах. `series` меток принимает bool/int/float. Ноль/false/na
скрывают метку; числовой ноль в `location.absolute` задаёт координату 0.
Цвет метки и `textcolor` могут быть series и хранятся независимо.
`title`/`text` — const string, стиль/location/char — const/input string.
Поддерживаются 12 закрытых `shape.*`, пять `location.*`, шесть `size.*`.
`char` содержит ноль или один Unicode code point; text — до 4096 code points.

Default offset=0, shape.xcross, location.abovebar и size.auto явно указаны
в pinned reference. Остальные недокументированные defaults выбраны OpenPine:
пустые title/text, char `★`, синий RGBA `#2196F3FF` для color/textcolor,
legacy bgcolor transp=90, marker transp=0. Это отдельные модельные решения,
а не утверждение о vendor defaults. Legacy transp меняет только opaque
альфу, включая textcolor; встроенная альфа сохраняется. `barcolor(na)`
оставляет прежний цвет; несколько вызовов выбирают последний non-na цвет.
Фоны смешиваются в исходном порядке с учётом альфы.

Первый ряд аргументов сохраняет legacy порядок до textcolor в v1–v4,
modern порядок без transp в v5/v6. Дополнительные title и size передаются
по имени, чтобы расширение параметров не меняло порядок позиционных аргументов.
editable, display, format, precision,
динамический offset и drawing objects вне этого профиля.

Typed IR `.23`, runtime state `.15`, metadata `openpine.signal-layer.1`
включаются только при использовании новых вызовов. Старые программы сохраняют
свои IR/state/output схемы. Числовые значения и оба цвета остаются привязаны
к исходному бару; offset -10000..10000 применяется только при рисовании.
В Parquet цвет и textcolor находятся в существующей строке value_json,
а постоянные параметры — в descriptor. Range API возвращает source-aligned
окно и ограниченный `halo` с источниками снаружи окна, чьи смещённые точки
попадают внутрь. Это сохраняет метки на границах страниц без перестановки данных.

В историческом UI полные OHLC рисуются свечами с общим цветом тела и тени.
Для close-only данных сохраняется линия close; barcolor не перекрашивает её
и не выдумывает OHLC. Метки и фон идут в основной график при overlay=true,
иначе в панель результата. В v5–v6 `bgcolor`, `plotshape`, `plotchar` и `plotarrow`
принимают именованный const bool `force_overlay`. Значение `true` переносит
слой на ценовую панель в истории и live. `barcolor` всегда относится
к ценовой панели.
[Правила размещения](DRAWING_OVERLAY.md)
Abovebar/belowbar в основном графике привязаны к high/low целевого бара,
с fallback на close; в отдельной панели используются верхняя/нижняя границы.
Top/bottom привязаны к границам панели, absolute — к числовой шкале.
Формы/шрифты и раскладка являются нашим renderer, без pixel equivalence.

## Стрелки и последние N баров

`plotarrow` принимает числовой ряд. Положительное значение рисует
стрелку вверх под целевым баром, отрицательное — вниз над баром.
Ноль и `na` пропускают стрелку. На ценовой панели берём минимум
или максимум свечи, а для данных close-only используем close. В
панели индикатора стрелки привязаны к нижней и верхней границам.
Модуль значения управляет высотой в пикселях. Пределы по умолчанию
5 и 100, допустимы `1 <= minheight <= maxheight <= 1000`.
Малая панель ограничивает высоту, чтобы стрелка оставалась видимой.

В v1–v3 цвета направления константные, в v4–v6 допускают series color.
В v1–v4 `transp` занимает пятый позиционный аргумент. С v5
именованный `force_overlay` переносит слой на ценовую панель.
`show_last` передаётся по имени и применяется к исходным индексам
до `offset`. Отсчёт идёт от конца результата или текущего replay.
Ноль скрывает метки, пропущенный аргумент и `na` показывают весь ряд.
Те же правила окна действуют для `bgcolor` и `barcolor`.
API и экспорт сохраняют исходные значения и цвета. У стрелок
дескриптор результата хранит `max_abs` для всего подтверждённого ряда.
[ADR 0029](adr/0029-signal-arrow-window.md) · [Справка Pine](https://www.tradingview.com/pine-script-docs/visuals/text-and-shapes/#plotarrow)
[Окна отображения](VISUAL_WINDOWS.md)

Проверки: `tests/test_visual_signals.py`, `tests/ui/signals.html`,
`conformance/visual-signals/`, шесть `signals-routing-v1..v6.pine`.
Installed helper markers в прежней signals suite проверяет source/saved IR,
CLI/HTTP, worker pause/resume, замороженные color inputs, Parquet, оба цвета
и положительный/отрицательный halo. Общие лимиты suites/shards не увеличены:
180 программ занимают девять shard по 20; последующее расширение корпуса
потребует отдельно пересмотреть распределение.

Первичные материалы: [backgrounds](https://www.tradingview.com/pine-script-docs/visuals/backgrounds/),
[bar coloring](https://www.tradingview.com/pine-script-docs/visuals/bar-coloring/),
[text and shapes](https://www.tradingview.com/pine-script-docs/visuals/text-and-shapes/).
