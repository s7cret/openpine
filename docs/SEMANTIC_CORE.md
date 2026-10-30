# Литеральные defaults UDF и switch

Первая порция расширения семантики после диагностики. Профиль
`pine-semantic-core-20261030.1` дополняет прежние неизменяемые bundles.
Используются те же typed IR, evaluator, история, rollback и checkpoints.

Локальная Wiki содержит switch в v5/v6, `wiki:v5:udf-default-parameters` и
`wiki:v6:udf-parameters`. Исходные SHA и URL закреплены в
`spec/semantic-core-profile.json`. [Release notes v5](https://www.tradingview.com/pine-script-docs/v5/release-notes/)
подтверждают появление defaults; [описание UDF](https://www.tradingview.com/pine-script-docs/language/user-defined-functions/)
описывает optional-параметры и явный тип для na. Это документальные
свидетельства, а не исполнения в TradingView. В v1–v4 новая syntax даёт
`FUNCTION_DEFAULT_VERSION` / `SWITCH_VERSION`; прежние реконструкции сохранены.

```pine
//@version=6
indicator("Semantic core")
scale(x, factor=2) => x*factor
route(x) =>
    switch
        x<=20 => scale(x)
        => scale(x,factor=3)
plot(route(close))
```

Для close 10,20,30,40 ожидается 20,40,90,120. Примеры каждой версии:
`examples/corpus/semantic-routing-vN.pine`.

## Defaults

Поддержаны scalar int/float/bool/string, числовой na с явным типом (также bool
na в v5) и знаковые числовые литералы. Значения проверяются даже в неиспользованных функциях и при явной
передаче аргумента. Пропуск обязательного параметра, неизвестное имя, дубли и
позиционный аргумент после именованного диагностируются статически.
Выражения defaults, globals/другие параметры и коллекции исключены.

Explicit-аргументы вычисляются один раз в исходном порядке; закрытые defaults
вставляются после них в порядке параметров. Compiler сохраняет полностью
связанный UserCallIR с существующим `origin=default`, `source_index=null`.
Runtime проверяет версию, literal-only форму, типы и порядок до эффектов.
FunctionContract описывает полностью связанную сигнатуру; optional-форма
остаётся в закреплённом исходнике. Схема IR сохраняется, вычислительная
идентичность меняется. Старые результаты читаются; старый несовместимый IR
не исполняется молча под новой идентичностью.

## Switch

Обе формы: `switch selector` и `switch` с условиями. Selector вычисляется один
раз в приватном блоке; выполняется первая подходящая ветка, без fallthrough.
Поддержаны inline/multiline блоки, вложенные switch, statement-эффекты,
последние значения UDF и плоские scalar-tuples. Области и состояние вызовов
используют существующие правила. Скрытое имя selector выбирается вне всех
имён исходника и не выходит из блока.

До 32 условных случаев; optional default только последний. Нужен хотя бы один
условный случай. Отсутствующий numeric match даёт na; bool — na в v5, false
в v6. String и tuple требуют default по ограниченному if-профилю. Ссылочные
результаты switch исключены. Statement-switch может не иметь default и
ничего не возвращать. Все ветки проверяются статически, исполняется одна.
Нормализованный AST ограничен глубиной 128 и 8192 узлами до рекурсивных
проверок. Развёрнутые value-ветки учитываются в общем бюджете Binder; чрезмерная
вложенность switch и цепочек UDF даёт `FUNCTION_EXPANSION`, а не Python RecursionError.

## Проверки

`tests/test_semantic_core.py`: defaults, порядок эффектов, один selector,
ленивые условия, независимость branch/UDF var, scopes, tuples, типы, codec,
отказ поддельного effectful default. `tools.semantic_core_spec --check` и
`tools.semantic_core_fixtures --check` входят в release gate. 22 независимых
conformance-случая и восемь целых программ дополняют прежние наблюдения.
Среда квалификации — Python 3.14.8.
