# Перечисления и настройки enum v5/v6

Профиль `openpine.enums.1` закреплён в `spec/enums-profile.json` по локальному
версионному bundle Wiki и руководствам [v5](https://www.tradingview.com/pine-script-docs/v5/language/enums/)
и [v6](https://www.tradingview.com/pine-script-docs/language/enums/). Это ограниченная
модель OpenPine; сравнение исполнения с TradingView остаётся отдельным этапом.

```pine
//@version=6
indicator("Enum routing")
enum Mode
    normal = "Normal"
    double = "Double"
mode = input.enum(Mode.normal, "Mode")
type Sample
    float price
    Mode mode = Mode.normal
method value(Sample s) =>
    s.mode == Mode.double ? s.price * 2 : s.price
var modes = array.new<Mode>()
modes.push(mode)
sample = Sample.new(close, modes.get(modes.size() - 1))
plot(sample.value())
```

## Семантика и границы

Enum объявляется глобально до использования; до 32 типов и 64 членов в типе.
Имена членов и их строковые подписи не пересекаются с именами и подписями
других членов. Без явной подписи используется имя. Разные enum номинально
различны, даже если содержат одинаковые имена. Присваивание, `==`, `!=`,
условия, `switch`, типизированный `na`, `na(value)`, история и
`str.tostring(value)` поддержаны. Преобразование в строку возвращает подпись,
для missing enum — `"NaN"`; арифметика, сравнение порядка и неявные
преобразования в строки или числа отвергаются.

Работают enum-параметры, результаты и прямые member defaults UDF, перегрузки,
методы, поля плоских UDT и `array<Enum>` через существующие Binder и runtime.
Массивы сохраняют прежние правила структурных операций, aliases, истории,
лимитов и версионных индексов. Enum-поля не создают новых рёбер GC.
`varip` enum и массивов переносится между обновлениями; у объектов переносятся
только поля с собственным `varip`, обычные поля откатываются.

Член enum имеет qualifier `simple` в профиле v5 и `const` в v6. Здесь есть
противоречие первичных описаний v5: руководство описывает члены как `simple`,
а справочник `input.enum` требует `const enum` для `defval`. OpenPine принимает
только прямой член enum как default input и явно сохраняет это расхождение.
Для `str.tostring` действует прежнее правило реализации: `series` для series
операнда, иначе `simple`. Эти решения не являются внешне подтверждённой
эквивалентностью.

Pine v1–v4 отвергает `enum` и `input.enum` с `ENUM_VERSION`; scalar-типы этих
версий сохраняются. Исключены библиотеки import/export, maps, matrices,
enum-dependent requests и enum tuples. Выражения для заголовков enum и
метаданных input ограничены литералами; общие default-выражения UDF исключены.

## Настройки и транспорт

`input.enum` требует прямого global declaration. `options` — непустой список
членов того же enum с включённым default; без списка доступны все члены.
Поддержаны title, tooltip, inline, group, `confirm=false`, а в v6 также
литеральный active. Настройка замораживается прежним execution-settings
контрактом и участвует в идентичности запуска, пресетов, экспериментов и поиска.

В IR и API значение — закрытый token `enum:<line>:<column>:<count>:<index>`.
Это scalar identity, проверяемая по реестру программы, а не пользовательская
строка или heap ID. CLI/HTTP overrides используют token из input schema;
произвольная подпись вместо него отвергается. Веб-настройки, пресеты,
оси поиска и строки результатов показывают подписи. Технический JSON
сохраняет точные tokens для воспроизводимости.

Enum-программы используют typed IR `.16` и runtime-state `.8`. Программы без
enum сохраняют прежние records. Restore проверяет номинальный тип, замкнутый
набор членов, поля, массивы, историю и intrabar значения до присоединения
состояния. Лимиты памяти и checkpoint не увеличены. Durable restore 100 тысяч
enum slots проверяется и внутри незакрытого бара; после restore runtime не
дочитывает checkpoint файлы.

## Проверки

`conformance/enums` содержит 82 ручных случая: 43 исполнения и 39 отказов.
Новые whole-program примеры — routing v1–v6 и enum strategies v5/v6. В v1–v4
routing использует числовые настройки; в v5/v6 — enum input, массивы, поля и
методы UDT. Прежние 96 записей корпуса сохраняются полностью.

Отдельные тесты проверяют повреждённые IR/checkpoint, подмену metadata input,
every-bar/every-event restore, scalar/array/field varip, HTTP/CLI/presets,
замороженные search candidates и паспорт возможностей. Браузерный harness
проверяет production JS с тестовым API; реальный HTTP/worker путь проверяется
отдельно. Результаты полной приёмки публикуются отдельным свидетельством.
