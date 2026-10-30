# Пользовательские объекты v5/v6

`spec/objects-profile.json` закрепляет профиль `openpine.flat-objects.1` по
версионному bundle Wiki и первичным описаниям [v5](https://www.tradingview.com/pine-script-docs/v5/language/objects/)
и [v6](https://www.tradingview.com/pine-script-docs/language/objects/).
`type` допускается в v5/v6; в v1–v4 выдаётся `UDT_VERSION`. Это ограничение UDT,
а не существовавших ранее scalar-типов `int`, `float`, `bool`, `string`.
Внешнее совпадение исполнения с TradingView остаётся отдельным этапом.

## Поддержанный срез

Глобально объявляются до 32 типов, каждый с 1–32 scalar-полями. Идентичность
номинальная: одинаковые поля двух разных типов не делают их взаимозаменяемыми.
Конструктор `T.new(...)` принимает позиционные и именованные аргументы.
Явные аргументы вычисляются один раз в исходном порядке, затем defaults в
порядке полей. Default — литерал, signed numeric literal либо совместимая
встроенная переменная; вызовы и произвольные выражения исключены.
Встроенная переменная читается при каждом создании объекта. Пропущенное поле
получает `na`, кроме `bool` в v6, для которого default — `false`.

```pine
//@version=6
indicator("Objects")
type Stats
    float total = 0.
    int count = 0
method add(Stats self, float value) =>
    self.total := self.total + value
    self.count := self.count + 1
    self
method mean(Stats self) => self.total / self.count
var stats = Stats.new()
alias = stats
alias.add(close)
snapshot = stats.copy()
plot(snapshot.mean())
```

Присваивание копирует ссылку: запись через alias видна владельцу. `.copy()` и
`T.copy(id)` создают отдельный объект с теми же scalar-значениями. Пользовательский
метод `copy` может заменить instance-вариант; явный `T.copy` сохраняет встроенный
смысл. Параметры и результаты UDF, условные результаты и методы используют
существующий Binder, выбор перегрузки и `UserCallIR`. `if` и `switch` возвращают
ссылку выбранной ветви; без совпадения/`else` результат — типизированный `na`.
Reference-типы имеют
qualifier `series`; `simple T` отвергается. Допускается типизированный `na`,
в том числе default параметра; чтение/запись поля `na` даёт `UDT_NA`.

## История и rollback

`(object[1]).field` хранит прошлую ссылку, а не копию полей. Если это прежний alias
изменённого объекта, чтение видит его текущее содержимое. Для истории scalar-значения
нужно сначала `value = object.field`, затем `value[1]` (текущий профиль истории:
numeric/bool). Прямая форма `object.field[1]` запрещена в v6. Для v5 она получает
`PROFILE_UNCOVERED`: [официальная миграция](https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/#history-of-udt-fields)
описывает старое поведение как ошибочное; его неизвестный результат не эмулируется.

Обычные поля откатываются на каждом незакрытом обновлении. Только поля с `varip`
переносят изменения между обновлениями; `varip` у переменной-ссылки не продвигает
обычные поля. Для ссылки, впервые созданной внутри незакрытого бара и сохранённой
через `varip`, обычные поля восстанавливаются к значениям конструктора, а изменения
помеченных полей переносятся. Последнее — явно закреплённая интерпретация OpenPine
для этого профиля, пока без внешней проверки. Every-event-cut тесты проверяют
непрерывный replay и восстановление, включая замену обычного alias и сохранение
изменённых `varip`-полей.

## Контракты и границы

`ObjectProgramIR` расширяет `ProgramIR` закрытым реестром типов; схема `.14`
содержит `ObjectIR` для new/copy/get/set/is_na. Старые программы сохраняют прежний
record и схему. Codec и runtime проверяют номинальные ID, scalar-поля,
происхождение defaults, порядок и однократность аргументов до эффектов.
Паспорт перечисляет новый профиль и операции `object.*`.

Объекты используют общий с массивами heap: единые IDs, roots, GC, квоты,
учёт памяти, COW previews и atomic restore. Лимиты не увеличены. Конструкторные
baseline учитываются в общем storage ledger. Нативный confirmed checkpoint не
принимает transient baseline; intrabar checkpoint проверяет, что обычные поля
не были подменены при переносе состояния. Трасса показывает ограниченный preview
полей через тот же механизм наблюдений.

Пока исключены reference-поля, вложенные/рекурсивные UDT, массивы объектов,
object-tuples, object-valued loop results, сравнение объектов, void-результаты и
libraries. Statement-loops с изменением полей работают. Object-dependent
`security`/`security_lower_tf`, request-цели и контексты отвергаются; независимые
чистые request-цели остаются доступны. Это частичный профиль объектов.

Независимые fixtures: `conformance/objects/cases.json`, 66 случаев — 28 исполнений
и 38 ожидаемых отказов. Owned corpus дополнен восемью программами: scalar routing
v1–v4, object routing v5/v6 и object strategies v5/v6. Все прежние 80 записей
сохраняются. Генераторы не исполняют Pine для получения ожиданий.
