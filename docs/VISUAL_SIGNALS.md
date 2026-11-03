# Раскраска и сигнальные метки

`openpine.visual-signals.1` добавляет `bgcolor`, `barcolor`, `plotshape`,
`plotchar` для Pine v1–v6. Правила закреплены в
`spec/visual-signals-profile.json` с исходными entry ID, basis, syntax,
типами и default claims из неизменяемого Wiki snapshot. V1 опирается на
primary archive; v2 — compatibility baseline/reconstruction; v3–v6 — primary.
Внутреннее соответствие этому профилю не устанавливает точного совпадения
с выполнением TradingView.

| Параметр | v1/v2 | v3/v4 | v5 | v6 |
| --- | --- | --- | --- | --- |
| Четыре функции | Да | Да | Да | Да |
| Именованные аргументы | Вне исторически проверенного профиля | Да | Да | Да |
| `transp` для bgcolor/меток | В legacy positional order | В legacy order | Только именованный | Недоступен |
| `size` для меток | Нет; внутренний auto | const, именованный | const, именованный | const, именованный |
| `offset` | const/input, ограниченное подмножество старого series offset | То же | То же | const/input, подмножество simple offset |

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
по имени, чтобы отсутствие editable/show_last не меняло positional смысл.
editable, show_last, display, format, precision, force_overlay,
динамический offset, plotarrow и drawing objects явно вне этого профиля.

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
иначе в панель результата; barcolor всегда относится к основному графику.
Abovebar/belowbar в основном графике привязаны к high/low целевого бара,
с fallback на close; в отдельной панели используются верхняя/нижняя границы.
Top/bottom привязаны к границам панели, absolute — к числовой шкале.
Формы/шрифты и раскладка являются нашим renderer, без pixel equivalence.

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
