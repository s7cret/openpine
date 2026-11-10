# Архитектурные решения OpenPine

| ADR | Решение | Статус | Дата записи |
| --- | --- | --- | --- |
| [0001](0001-project-scoped-codex-skills.md) | Четыре локальных навыка Codex вместо полной установки фреймворков | accepted | 2026-11-01 |
| [0002](0002-maps-on-shared-reference-heap.md) | Maps v5/v6 в общем reference heap | accepted | 2026-11-01 |
| [0003](0003-matrices-on-shared-reference-heap.md) | Матрицы v5/v6, форма, обход и общий heap | accepted | 2026-11-02 |
| [0004](0004-reference-fields-and-cyclic-heap.md) | Ссылочные поля UDT, циклы, GC и intrabar baseline | accepted | 2026-11-02 |
| [0005](0005-ephemeral-product-results.md) | Смешанные результаты, временные tuple и прежние codecs состояния | accepted | 2026-11-02 |
| [0006](0006-color-values-and-plot-presentation.md) | Типизированные цвета, представление plot и совместимость числовых рядов | accepted | 2026-11-03 |
| [0007](0007-declarative-visual-reference-graph.md) | Декларативный граф plot/hline/fill и цветовые настройки | accepted | 2026-11-03 |
| [0008](0008-source-aligned-visual-signals.md) | Сигнальные слои, два цвета и смещения через range halo | accepted | 2026-11-03 |
| [0009](0009-durable-labels.md) | Идентичность меток, rollback, снимок и журнал событий | accepted | 2026-11-03 |
| [0010](0010-shared-drawing-lifecycle.md) | Общий жизненный цикл линий и меток с отдельными форматами | accepted | 2026-11-03 |
| [0011](0011-line-projection.md) | Видимый диапазон линии и независимое вычисление цены | accepted | 2026-11-04 |
| [0012](0012-box-rendering.md) | Хранение углов и обрезка рамки ценовой зоны | accepted | 2026-11-04 |
| [0013](0013-current-pane-table-state.md) | Текущее состояние таблиц в панели и отдельный предпросмотр | accepted | 2026-11-04 |
| [0014](0014-linefill-boundary-ownership.md) | Заливка принадлежит паре линий | accepted | 2026-11-04 |
| [0015](0015-drawing-time-projection.md) | Хранение времени и единая ось рисунков | accepted | 2026-11-04 |
| [0016](0016-chart-point-reference.md) | Ссылки точек и снимки координат рисунков | accepted | 2026-11-05 |
| [0017](0017-polyline-vertex-snapshots.md) | Снимки вершин ломаных и лимиты | accepted | 2026-11-05 |
| [0018](0018-drawing-coordinate-mode-changes.md) | Атомарная смена режима X существующего рисунка | accepted | 2026-11-05 |
| [0019](0019-line-stroke-record.md) | Оформление и геометрия в общей записи линии | accepted | 2026-11-05 |
| [0020](0020-box-border-extension.md) | Геометрия бокса и продление рамки в окне | accepted | 2026-11-05 |
| [0021](0021-label-appearance-record.md) | Оформление и совместимость записей ценовых меток | accepted | 2026-11-06 |
| [0022](0022-label-bar-anchors.md) | Привязка меток к свечам окна | accepted | 2026-11-06 |
| [0023](0023-label-text-and-hover.md) | Настройки текста и область наведения метки | accepted | 2026-11-06 |
| [0024](0024-box-text-layout.md) | Текст, геометрия и окно бокса | accepted | 2026-11-06 |
| [0025](0025-drawing-pane-override.md) | Панель объекта и сохранение флага | accepted | 2026-11-07 |
| [0026](0026-linefill-pane-routing.md) | Панель заливки между двумя линиями | accepted | 2026-11-07 |
| [0027](0027-visual-output-panes.md) | Панели рядов и заливок между ними | accepted | 2026-11-07 |
| [0028](0028-visual-ohlc-layers.md) | Контракт и хранение вычисленных OHLC-слоёв | accepted | 2026-11-07 |
| [0029](0029-signal-arrow-window.md) | Высота стрелок и окно сигнальных меток | accepted | 2026-11-07 |
| [0030](0030-visual-output-windows.md) | Окно отображения и координаты заливки | accepted | 2026-11-08 |
| [0031](0031-plot-appearance.md) | Архивные сигнатуры plot и опорная линия | accepted | 2026-11-08 |
| [0032](0032-gradient-fill.md) | Градиентная заливка и её маска | accepted | 2026-11-08 |
| [0033](0033-visual-display.md) | Видимость ряда и доступность его значений | accepted | 2026-11-08 |
| [0034](0034-plot-destinations.md) | Места вывода числового plot и привязка числа к свече | accepted | 2026-11-09 |
| [0035](0035-plot-family-display.md) | Места вывода меток и OHLC | accepted | 2026-11-09 |
| [0036](0036-number-format.md) | Формат графика как метаданные результата | accepted | 2026-11-09 |
| [0037](0037-chart-scale.md) | Панель и шкала как отдельные свойства вывода | accepted | 2026-11-09 |
| [0038](0038-visual-order.md) | Порядок рисования и положение относительно рынка | accepted | 2026-11-09 |
| [0039](0039-drawing-limits.md) | Ёмкость графических объектов из декларации | accepted | 2026-11-09 |
| [0040](0040-drawing-inventory.md) | Снимки живых графических объектов | accepted | 2026-11-10 |
| [0041](0041-drawing-property-access.md) | Доступ к полям и отсутствие графического объекта | accepted | 2026-11-10 |
| [0042](0042-table-style-and-relocation.md) | Оформление и перенос таблиц | accepted | 2026-11-10 |
| [0043](0043-table-cell-layout.md) | Размеры и типографика ячеек таблиц | accepted | 2026-11-10 |

Новые записи ведутся по [навыку openpine-adr](../../.agents/skills/openpine-adr/SKILL.md).
Старые обоснования сохраняются; заменяющая запись связывается с предыдущей.
