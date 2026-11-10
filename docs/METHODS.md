# Методы v5/v6

`spec/methods-profile.json` закрепляет ограниченный профиль методов. В v1–v4
объявление `method` и instance-вызов дают `METHOD_VERSION`. По
[release notes v5](https://www.tradingview.com/pine-script-docs/v5/release-notes/#february-2023)
методы появились в феврале 2023. Правила сверены с
[описанием v5](https://www.tradingview.com/pine-script-docs/v5/language/methods/)
и [описанием v6](https://www.tradingview.com/pine-script-docs/language/methods/).
Точное совпадение исполнения с TradingView пока не проверено.

## Допуск и выбор

Первый параметр обязателен и явно типизирован: `int`, `float`, `bool`, `string`
либо массив одного из этих типов; [профиль объектов](OBJECTS.md) добавляет UDT.
Receiver выбирается по точному базовому типу;
автоматическое `int → float` допускается только для остальных аргументов.
Именованные аргументы, литеральные defaults, qualifiers, captures и допустимые
результаты используют существующий профиль UDF. Возвращаются scalar, плоский
tuple или primitive array; тело с результатом `void` пока отвергается.
Метод может менять массив и вернуть его для следующего вызова.

```pine
//@version=6
indicator("Methods")
method enqueue(array<float> self, float value, int cap=3) =>
    self.push(value)
    if self.size() > cap
        self.shift()
    self
var values = array.new_float(0)
plot(values.enqueue(close).avg())
```

Доступны instance-формы существующих `array.size/get/set/push/pop/shift/unshift`,
`clear/copy/sum/avg/min/max`. Сохраняются типовые ограничения каждой операции,
v6 negative indices и прежние лимиты heap. Конструкторы остаются namespace API.
Обычная функция и метод одного имени независимы. Пользовательский метод
вызывается только через точку в этом срезе.

Binder сначала сравнивает форму вызова, затем минимальное число numeric
widenings. Пользовательский метод при равной стоимости заменяет built-in;
равные пользовательские кандидаты дают `METHOD_AMBIGUOUS`. Объявления используют
версионную политику [перегрузок](FUNCTION_OVERLOADS.md): все параметры в v5,
обязательные в v6. Именно ранжирование, эта интерпретация арности и dot-only
допуск — закреплённые решения OpenPine, без утверждения внешней эквивалентности.

Известные namespace-вызовы сохраняют смысл: `array.avg(a)` обходит одноимённый
метод. Если переменная названа как namespace, `(math).f()` явно задаёт receiver.
Неиспользованные тела проходят структурные проверки и проверки определённых
зависимостей; типозависимые рёбра проверяются при специализации написанного вызова.
Рекурсия запрещена. Сохраняются прежние бюджеты определений и специализаций.

## Методы графических объектов

В Pine v5–v6 допущенные операции `table`, `line`, `label`, `box`,
`linefill` и `polyline` можно вызывать через значение соответствующего
типа. Например, `dash.merge_cells(0, 0, 1, 0)` использует сигнатуру
`table.merge_cells(dash, 0, 0, 1, 0)`, а `shade.get_line1().get_price(3)`
вызывает геттеры двух разных типов. Конструкторы вызываются через namespace.
[Справка Pine v5](https://www.tradingview.com/pine-script-docs/v5/language/methods/#built-in-methods) ·
[Справка Pine v6](https://www.tradingview.com/pine-script-docs/language/methods/#built-in-methods)

Компилятор подставляет объект первым аргументом и использует тот же
Binder и Typed IR. Объект вычисляется один раз перед явно написанными
аргументами. Сигнатуры, ограничения графических эффектов и порядок
вычисления именованных аргументов берутся из обычного вызова.
В v1–v4 действует `METHOD_VERSION`. Метод, отсутствующий у данного
типа или в версии скрипта, получает `METHOD_UNAVAILABLE`.
[Статическая матрица](PINE_FUNCTION_COVERAGE.md) · [Парные сценарии](../conformance/tables/cases.json)

## Исполнение и свидетельства

Receiver вычисляется один раз перед явными аргументами в исходном порядке;
defaults добавляются последними. Методы снижаются в существующие `UserCallIR`
и `ArrayIR`. Typed IR `.13` добавляет признак метода в контракт определения,
выбранный ID и required mask. Runtime проверяет также источник и порядок
implicit receiver; диспетчеризации методов во время исполнения нет.
Built-in sugar без пользовательских методов сохраняет прежнюю схему IR.

Состояние принадлежит написанной цепочке вызовов. Сохраняются алиасы,
var/varip, rollback и checkpoints; `a[1].get(i)` читает прошлую ссылку на объект,
а `a.get(i)[1]` — прошлый scalar-результат. Private request-граф сохраняет
выбранный контракт метода; чистые frozen targets используют существующий
ограниченный evaluator и прежние бюджеты. Глобальные plot-вызовы в теле
по-прежнему запрещены. Новых heap, scheduler или механизма восстановления нет.

`test_methods.py`, дополнительные проверки `test_context_graph.py` и
`test_intrabar.py` проверяют связывание, forged IR и восстановление в каждом
внутреннем разрезе. `conformance/methods` содержит 44 независимых случая:
20 исполнений и 24 отказа. В owned corpus добавлены routing v1–v6
(v1–v4 используют обычные функции) и стратегии v5/v6. Все прежние 72 записи
сохранены точно. UDT, enums, maps/matrices, произвольные defaults, новые
array-операции и void-return methods остаются вне этого профиля.
