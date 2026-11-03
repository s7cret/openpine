# Приёмка раскраски и сигнальных меток

Дата: 2026-11-03. Статус: passed.

Первый кандидат feaa5a2/CI41643498260 остановлен после обнаружения
устаревшего счётчика в тесте паспортов: ожидались 168 допущенных программ вместо 174.
Исправлено только это ожидание. Остальные проверки и все идентификаторы сохранены.
Его CI артефакты исключены; ниже — отдельная свежая полная приёмка.

[Исходник 2b270a059892](https://github.com/s7cret/openpine/commit/2b270a0598921b81bd6b630a88f325cb01321ee6), ветка `r6-rebuild`.
[CI 41654085990](https://github.com/s7cret/openpine/actions/runs/41654085990), попытка 1: **57/57 jobs**.
Следующий docs-only коммит с этой записью ссылается на этот исходник;
его документация и новый wheel отдельно этой проверкой не квалифицируются.

CPython 3.14.8: 1826 test ID / 179 модулей на ОС.
Windows: 1826 passed / 0 skipped;
Linux: 1825 passed / 1 штатный Windows8.3 skip.
Новый модуль: 9 тестов, включая каждый разрез native,
realtime и strategy recalculation, IR admission, enum/heap/fill composition,
Parquet prefix resume, оба цветовых канала и halo на границах окна.

Conformance: 3698 случаев (2329 исполнений / 1369 ожидаемых отказов),
51 отчёт побайтно совпал на двух ОС и локальном исходнике.
Новый профиль: 55 случаев (18 / 37).
Корпус: 180 программ (174 / 6), шесть новых routing v1–v6.
Полное переносимое свидетельство совпало между Windows/Linux:
1250 passed comparisons и 62 явно сохранённых model differences на ОС.

Сохранены 1817 старых test ID, 3643
conformance-наблюдений кроме program identity, 174 полных записей корпуса,
байты 129 прежних spec-файлов. Program-qualified state/strategy-ID
hash transitions перечислены отдельно; независимые oracles, порядок и причины различий не нормализованы.

Общий source-bound wheel `c73a7ef92445e405f225e7f5397fbb1f122c8fb9ceb4f7f1fa98d59d31ae2e14` использован во всех
18 installed suites и четырёх recovery proof. Новый installed helper проверил
CLI/source/saved IR, HTTP/worker pause-resume, замороженные inputs, Parquet,
оба RGBA канала и положительный/отрицательный offset halo для всех версий.
Локальный Chrome проверил canvas pixels, все 12 форм, alpha/na, OHLC тело/тень,
source order, numeric zero, text/char, смещения и фактические app chart/draw функции:
overlay, отдельную панель и close-only fallback. Прежние colors/bands browser tests прошли.
SHA этих UI-файлов совпадают с квалифицированным исходником.
Локальный общий модуль корпуса остановился на CoordinatorBusy host_memory
при чтении отчёта; адресный повтор сохранил тот же отказ. Лимиты не менялись.
Этот модуль, не менявшийся после локального отказа, подтверждён CI хостами;
локальный отказ и замер памяти сохранены в local_resource_notes.

Recovery с прежними нагрузками: managed/store — по 100000 баров;
unified — 333334 primary bars, 1000002 events, 10000020 output points;
realtime — 256 баров по 64 updates. Output points не означают 10 миллионов свечей.
Корпус занимает девять shards по 20 программ; CLI90s/worker120s/corpus1200s,
max-parallel12 и число installed suites не изменены.

[Правила и ограничения](VISUAL_SIGNALS.md),
[ADR0008](adr/0008-source-aligned-visual-signals.md),
[машинное свидетельство](evidence/visual-signals-20261103.json).
В архивных v1/v2 указан результат plot; этот ограниченный профиль
использует вызов только как глобальный output effect и не вводит программируемый handle.
Профиль ограничен static offsets и global calls; неизвестные defaults явно
отделены от pinned claims. Dynamic offsets, plotarrow, drawing objects,
полные display/editable настройки и внешняя TradingView equivalence остаются отдельной работой.
