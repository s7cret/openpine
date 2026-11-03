# ADR-0007: Декларативный граф plot/hline/fill

Дата: 2026-11-03. Статус: accepted.

Пользователь одобрил этап цветовых inputs, уровней и зон через всю цепочку
исполнения. Ссылки plot/hline имеют идентичность объявления и должны переживать
replay/restore без создания нового drawing object на каждом баре. Предыдущий
[ADR-0006](0006-color-values-and-plot-presentation.md) сохраняет числовой ABI.

Выбран закрытый граф ссылок, принадлежащий программе: contracts владеет типами,
сигнатурами, schema и валидацией графа; Binder допускает прямые глобальные
декларации/aliases; runtime выпускает стабильные callsite tokens. Fill принимает
только уже объявленные endpoints одного вида. Передача handles через heap,
UDF, history или mutable bindings запрещена в этом bounded профиле.

Typed IR `.22`, state `.14` и visual-layer `.1` добавлены отдельно; прежние
схемы автоматически не повышаются. Storage использует тот же ResultSink и
Parquet: hline — числовые строки, fill — null value с series color и ссылками
в descriptor. Это сохраняет потоковую запись и ограниченные range queries,
но повторяет постоянное значение hline по барам. Компактное хранение уровней
можно пересмотреть при измеренном объёме таких результатов.

Общий mutable drawing heap здесь добавил бы GC/alias/rollback состояния для
объектов, чья идентичность известна до исполнения. Для линий/labels/boxes в
будущем потребуется самостоятельное решение; этот граф не расширяется на них
неявно. Сервер не передаёт строки handles в пользовательские числовые ряды.

Источники установили отдельную границу input color: официальное
[объявление v4](https://www.tradingview.com/blog/en/create-color-inputs-in-pine-20751/)
и [перечень v3](https://www.tradingview.com/pine-script-docs/v3/annotations/script-inputs/).
Поэтому v1–v3 явно отклоняют цветовые inputs, сохраняя остальные визуальные
конструкции. `nz(color)` без замены — transparent black по
[type system](https://www.tradingview.com/pine-script-docs/language/type-system/).
V1/v2 и раннее поведение gap bridging помечены реконструкцией, внешних прогонов нет.

Закреплённые claims устанавливают fillgaps=false в v4–v6, const bool в v5/v6
и const color для hline v3. В v4 профиль консервативно принимает const bool;
pre-v4 default false реконструирован из первой документированной v4 формы.
Генератор проверяет эти ограничения непосредственно против pinned reference.
Default цвета/стиля hline, title и legacy fill transp в справочнике не описаны:
OpenPine явно выбирает blue/solid из принятого plot-профиля, пустой title и transp90.
Это обеспечивает воспроизводимый офлайн профиль; внешнее совпадение не заявлено.
Фразу «constant color» в prose v5/v6 трактуем как неизменность между барами:
тип input color в справочнике и [официальная FAQ](https://www.tradingview.com/pine-script-docs/faq/visuals/)
разрешают inputs. В v3 сохранён явно записанный qualifier const color.
Старый integer linestyle допускается только через известные именованные символы,
отображённые во внутренний закрытый тип; произвольные числовые коды не принимаются.

Границы и проверки изложены в [VISUAL_BANDS](../VISUAL_BANDS.md). Неизменяемые
Wiki bundle/предыдущие profiles сохранены; новый профиль содержит ссылки на
их evidence. При добавлении UDF handles, новых fill overloads или собственного
хранилища уровней потребуется пересмотреть граф, schema и restore admission.
