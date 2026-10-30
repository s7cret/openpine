# Массивы пользовательских объектов v5/v6

Профиль `openpine.object-arrays.1` дополняет плоские [UDT](OBJECTS.md) и
существующий [heap массивов](ARRAYS.md). Правила закреплены в
`spec/object-arrays-profile.json` по версионному bundle Wiki и первичным описаниям
[v5 arrays](https://www.tradingview.com/pine-script-docs/v5/language/arrays/),
[v6 arrays](https://www.tradingview.com/pine-script-docs/language/arrays/) и
[objects](https://www.tradingview.com/pine-script-docs/language/objects/).
Внешняя проверка исполнения на TradingView остаётся отдельным этапом.

```pine
//@version=6
indicator("Queue")
type Sample
    float price
    int index
method enqueue(array<Sample> self, Sample item) =>
    self.push(item)
    if self.size() > 3
        self.shift()
    self
var samples = array.new<Sample>()
samples.enqueue(Sample.new(close, bar_index))
total = 0.
for item in samples
    total := total + item.price
plot(total / samples.size())
```

## Типы и операции

Допускаются `array<T>`, `array.new<T>(size, initial_value)`, `array.from`,
типизированные `na` переменной и элементов, где `T` — уже объявленный UDT
с полями `int`, `float`, `bool`, `string`. У двух типов с одинаковыми полями
разные номинальные ID: их массивы не взаимозаменяемы. Reference qualifier —
`series`. Аргументы вычисляются один раз в порядке исходника.

Работают namespace/instance формы `size/get/set/push/pop/shift/unshift/clear/copy`,
`na`, UDF, перегрузки, пользовательские методы и `for…in` по элементам либо
индексу и элементу. Это прежние `ArrayIR`, Binder и runtime, без отдельного
интерпретатора. Числовые агрегаты над объектами отвергаются. Индексы сохраняют
правила существующего профиля: отрицательные разрешены в v6, запрещены в v5.
Пустой pop/shift даёт `ARRAY_EMPTY`, missing array — `ARRAY_NA`, чтение поля
missing object — `UDT_NA`. Default `=na` у параметра массива пока исключён,
как и для primitive arrays; типизированные переменные и элементы поддержаны.

Присваивание массива сохраняет ID контейнера. `array.copy()` создаёт отдельный
контейнер с прежними ID объектов: изменение поля видно через обе копии,
а замена элемента или clear затрагивает только выбранный контейнер.
`object.copy()` создаёт отдельную запись со скопированными scalar-полями.
`array.new<T>(n, object)` повторяет одну ссылку, а не создаёт n объектов.

## Владение и восстановление

Общий heap хранит массивы и записи с едиными ID. Persistent/history roots
удерживают контейнеры; каждый живой контейнер удерживает каждую ссылку своих
слотов один раз независимо от числа aliases контейнера. Удаление контейнера
освобождает его рёбра и недостижимые записи. Циклов нет: рёбра направлены только
из массивов в записи со scalar-полями. Reference-поля, вложенные массивы,
рекурсивные UDT, object tuples и object-dependent requests исключены.

Программы с object arrays используют typed IR `.15` и runtime-state `.7`.
Плоские объекты сохраняют `.14`; primitive arrays сохраняют прежние схемы.
Restore проверяет реестр, номинальные типы, все рёбра, достижимость, корни,
квоты и forward references до присоединения состояния. Недопустимы dangling
ID, ссылка на массив вместо записи, чужой nominal ID и недостижимые строки.
Восстановленные буферы не разделяются с JSON вызывающего кода.

Лимиты не увеличены: 100 тысяч элементов в массиве, 100 тысяч живых объектов,
миллион суммарных ячеек, 16 МиБ string payload, миллион операций на бар.
Ссылочный слот учитывается как `reference` (384 байта), поля и baseline —
через прежний scalar ledger; одновременно существующие COW/staging состояния
занимают общий бюджет. Тест на 100 тысяч одинаковых ссылок восстанавливает
одну запись и один контейнер, без размножения объектов.

## История и intrabar

`a[1]` хранит прошлый ID массива, а не снимок его содержимого. COW previews
изолируют изменения до подтверждения. `varip` массива переносит структуру
контейнера между обновлениями, но обычные поля объектов откатываются.
Переносятся только поля с собственным `varip`. Это действует и для объектов,
достижимых через обычный массив; изменение обычной структуры откатывается.

Для впервые созданной intrabar записи обычные поля восстанавливаются к
значениям конструктора. Это явно закреплённая интерпретация OpenPine без
внешнего подтверждения. Partial checkpoint проверяет baseline и поле за полем
не допускает перенос обычной мутации под видом `varip`. Aliases и рёбра
перенумеровываются согласованно, без накопления transient ID.

## Свидетельства

`conformance/object-arrays` содержит 68 ручных случаев: 40 исполнений и
28 отказов. `test_object_arrays.py` проверяет aliases, COW, GC, квоты,
поддельные checkpoint, every-event restore и большую очередь ссылок.
Корпус дополнен восемью owned-программами: routing v1–v6 и queue strategies
v5/v6. В v1–v3 routing использует scalar-аналоги, в v4 primitive arrays.
Все прежние 88 записей манифеста сохранены полностью.

В старом objects-corpus два случая `v5-reject-object-array` и
`v6-reject-object-array` теперь исполняются: исходники и case ID сохранены,
изменилось ожидаемое поведение после допуска новой возможности. Остальные
прежние наблюдения сравниваются точно, кроме идентичности реализации.

`tools.installed_lot_smoke` записывает wall time фаз всех 18 программ и research
маршрутов в CI receipt при прежнем worker limit 120 секунд. Отдельный
`python -m tools.lot_checkpoint_probe --output probe.json` измеряет исполнение,
export, checkpoint codec/durable IO и restore на conditional-v3, с ручным
оракулом и паузой после бара 5. Его область — driver и storage; запуск процесса,
координатор и публикация manifest в это измерение не входят.
