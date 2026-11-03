# Приёмка цветовых настроек, уровней и зон

Дата: 2026-11-03. Статус: passed.

[Исходник f798d683574e](https://github.com/s7cret/openpine/commit/f798d683574e3353d70fcff8b03b4985f7bdaebd), ветка `r6-rebuild`.
[CI 41622429850](https://github.com/s7cret/openpine/actions/runs/41622429850), попытка 1: **57/57 jobs**.
Поздний docs-only коммит с этой записью ссылается на проверенный исходник;
его текст и новый wheel этой проверкой заново не квалифицируются.

CPython 3.14.8: 1817 test ID / 178 модулей на ОС.
Windows: 1817 passed, 0 skipped;
Linux: 1816 passed, 1 штатный Windows 8.3 skip.
Новые тесты: 16; восстановление native/realtime/strategy после
каждого разреза событий, exact reference graph, forged IR и heap/enum composition.

Conformance: 3643 сценариев (2311 исполнений / 1332 ожидаемых отказов),
50 побайтно одинаковых отчётов между ОС и опубликованным исходником.
Новый набор: 43 случая (18 / 25).
Корпус: 174 программы (168 / 6), включая routing v1–v6,
RSI zones и MA channel. Полные переносимые свидетельства совпали между ОС:
1208 passed comparisons и 62 сохранённых model differences на ОС.

Сохранены 1801 прежних test ID, все 3600
conformance-наблюдений кроме program identity, 166 полных записей
корпуса и байты 128 старых spec-файлов. Для переносимых записей
прежнего корпуса отдельно перечислены program-qualified state/strategy-ID hash
transitions; independent oracles и причины model differences не нормализованы.

Один общий source-bound wheel `0e6334ff0f1c6cddf7b8473a41e209c4fae0acc4fa463bebc6ee14b8a9b64fba`.
Installed CLI/source/saved IR и HTTP/worker pause/resume проверены для v1–v6;
цветовые inputs/presets для v4–v6. Редактирование пресета во время паузы не меняет
закреплённые RGBA. References и цвета сохранены в Parquet, full result и chart range.
Локальный Chrome проверил реальные пиксели, fillgaps, альфу, missing color, dashed
hline и picker/preset/reset/lock; SHA UI-файлов совпадают с квалифицированным source.

Все четыре recovery proof прошли с прежними нагрузками: managed/store — 100000
баров; unified — 333334 primary bars, 1000002 events и 10000020 output points;
realtime — 256 баров по 64 updates. Output points не означают 10 миллионов свечей.
Сохранены 18 installed suites, девять corpus shards на ОС, CLI90s, worker120s,
corpus1200s и max-parallel12. Корпус распределён по 19–20 программ на shard.

До приёмки исправлены whitelist strategy preview и устаревшее ожидание размера
пачки корпуса 38→46. Первый локальный spec run остановлен перед исправлением
тестового source; окончательная спецификация и CI выполнены заново.
Исходные логи сохранены в proof directory; тесты и лимиты не отключались.
Git перед публикацией потребовал LF в tools/bands_fixtures.py. После нормализации
повторена спецификация; байты всех 5778 исходных файлов в Git index сверены с кандидатом.
Кандидат 9d53079 / CI41608262161 остановлен до приёмки: подменённый persistent
hline_style принимался admission, но restore падал с KeyError. Воспроизведение
сохранено; новый источник отклоняет такой IR до выполнения и проверен заново
с отдельным wheel. Const/input выбор стиля разрешён, series style отклонён.
Кандидат cfe71b1 / CI41617111987 также остановлен до приёмки после сверки pinned
claims: исправлены fillgaps default=false/const bool и const color hline v3.
Недокументированные defaults явно отделены от требований источника.
Генератор сверяет эти известные claims непосредственно с pinned reference.

[Правила и ограничения](VISUAL_BANDS.md),
[ADR 0007](adr/0007-declarative-visual-reference-graph.md),
[машинное свидетельство](evidence/visual-bands-20261103.json).
Inputs color недоступны v1–v3. Ранние детали визуального профиля реконструированы;
mutable handles, gradient fill overloads и внешняя эквивалентность TradingView
остаются отдельными этапами.
