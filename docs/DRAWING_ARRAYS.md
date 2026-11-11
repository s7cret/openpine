# Массивы ID рисунков

В Pine v4–v6 индикаторы и стратегии могут хранить ID линий, меток и боксов
в изменяемых массивах. В v4 используются `line[]`, `label[]`, `box[]`
и конструкторы `array.new_line`, `array.new_label`, `array.new_box`
с обязательным размером. В v5–v6 доступны `array<T>`, `array.new<T>()`
и массивы полилиний. Для v1–v3 действует граница массивов `ARRAY_VERSION`.
[Массивы Pine v4](https://www.tradingview.com/pine-script-docs/v4/essential/arrays/) ·
[Примеры Pine v5](https://www.tradingview.com/pine-script-docs/v5/concepts/lines-and-boxes/)

```pine
//@version=6
indicator("Recent levels", overlay=true)
var array<line> levels = array.new<line>()
levels.push(line.new(bar_index, close, bar_index + 1, close))
if levels.size() > 3
    line.delete(levels.shift())
```

Доступны `size`, `get`, `set`, `push`, `pop`, `unshift`, `shift`,
`clear`, `copy` и `na`. С v5 доступны `first`, `last`, методы массива
и `for…in`. С v6 индексы могут отсчитываться от конца. Пустой массив
при чтении или извлечении крайнего элемента выдаёт `ARRAY_EMPTY`.
[Семантика массивов Pine](https://www.tradingview.com/pine-script-docs/language/arrays/)

Обычные привязки и `var` используют общие лимиты heap. Присваивание сохраняет
тот же массив, а `array.copy` создаёт отдельный массив тех же ID.
История `ids[1]` возвращает прошлую ссылку на массив. Удаление рисунка
влияет на его состояние в хранилище рисунков. ID сохраняется в массиве
до явного изменения массива. `*.all` возвращает снимок живых ID для чтения.
[Снимки](DRAWING_OBJECTS.md) · [Лимиты](DRAWING_LIMITS.md)

Массивы и их ссылки восстанавливаются из checkpoint. Изменения на открытом
баре видны в preview и откатываются перед следующим пересчётом.
Ограничения профиля и проверка ID описаны в [ADR 0046](adr/0046-drawing-array-storage.md).
[Индикатор v4](../conformance/arrays/v4-drawing-queue.pine) ·
[Стратегия v6](../conformance/polylines/v6-strategy-drawing-queue.pine)
