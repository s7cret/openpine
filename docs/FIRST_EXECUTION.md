# Первый сквозной запуск R6

```pine
//@version=6
indicator("R6 first run")
plot(math.abs(-2.5))
```

Программа из `conformance/literal-harness/abs-float.pine` проходит разбор, статическое разрешение, lowering и исполнение, возвращая float `2.5`. Это собственная реализация OpenPine; код прежнего compiler/reference consumer не переносился.

## Запуск на Python 3.14.8

Из корня репозитория:

```sh
python -m pip install -r requirements-dev.txt
python -m apps.openpine run --source conformance/literal-harness/abs-float.pine
python -m apps.openpine compile --source conformance/literal-harness/abs-float.pine
python -m tools.release_gate
```

CLI выдаёт UTF-8 JSON. `run` содержит `status=executed`, `result.outputs[0].values=[2.5]`, сведения о декларации, identity и IR. `compile` содержит `status=compiled`, тот же IR и `result=null`; исполнение при этом не запускается. `--output result.json` сохраняет JSON. Код выхода 2 означает диагностированный отказ.

Команда CLI пока запускается из исходного дерева; устанавливаемого дистрибутива и отдельного shell entry point ещё нет.

## Путь и границы модулей

1. `packages/compiler/frontend/parser.py` использует token/directive patterns закреплённой грамматики. Он строит AST с позициями, не выполняя исходный код.
2. `packages/compiler/semantic/binder.py` один раз разрешает каждый исходный аргумент по принятым машинным сигнатурам. Он проверяет имена, число, типы, qualifiers и defaults. Привязанные параметры сохраняются в порядке сигнатуры; порядок вычисления исходных аргументов хранится отдельно.
3. `packages/compiler/lowering/` создаёт immutable Typed IR. Общая форма IR, типы, позиции и диагностики находятся в `packages/contracts`.
4. `packages/runtime/engine.py` проверяет совместимость всего IR перед исполнением. Runtime вызывает разрешённые operation IDs и использует уже привязанные аргументы; исходные имена/defaults он повторно не разрешает. В runtime нет импортов compiler, tools или приложения.
5. `packages/execution/session/` связывает compiler, допущенную спецификацию и capabilities runtime. Приложение находится в `apps/openpine`.

Каждая программа закрепляет исходник, spec manifest, весь spec lock, semantic profile и hash реализации. Последний включает Python-исходники `packages`/`apps`, версию Python и валидатора схем. Public IR имеет версию `openpine.typed-ir.2`: добавлены разрешённые context references и явное преобразование int → float. Сейчас поддерживается только передача typed objects внутри процесса. JSON из `compile` не является обещанием загрузки старых артефактов или checkpoint/resume.

## Объём реализации

Implementation profile: `openpine.literal-harness.1`, semantic profile: `pine6.core-mvp.1`.

Поддержаны числовые/bool/string литералы, скобки, унарные `+`/`-`, вложенные `math.abs`, именованные/позиционные аргументы и явные defaults трёх harness-сигнатур. `math.abs` сохраняет тип int/float; bool не считается числом. Декларация записывает title/shorttitle/overlay; каждый plot получает собственный output ID, в том числе при одинаковом title.

В literal-профиле исполнение состоит из одного синтетического наблюдения, без баров и временных меток. Отдельный [исторический профиль](HISTORICAL_EXECUTION.md) поддерживает `close` и EMA на локальных барах. `indicator`/`plot` — ограниченный наблюдательный harness. Plot handles, пользовательские переменные, binary operators, оператор истории, запросы, стратегии, библиотеки, realtime и worker требуют следующих этапов. Известный контракт без kernel даёт `IMPLEMENTATION_MISSING`; отсутствующий принятый контракт — `SPEC_UNRESOLVED`; конструкция вне среза — `PROFILE_UNCOVERED`. Это отдельные случаи от установленной ошибки `LANGUAGE_INVALID`.

Входной Pine v1–v5 отвергается с указанием своей версии и отсутствующей реализации. Без аннотации выбирается v1. Строки и обычные комментарии с текстом `//@version` версию не переключают.

Числа ограничены конечными float и знаковым 64-битным int; неподдержанная граница сообщает `PROFILE_UNCOVERED`, не заменяется другим значением. Для этого среза действуют лимиты: 64 KiB исходника, 4096 токенов и ограниченная вложенность выражений. Строковые escapes: `\\`, `\"`, `\'`, `\n`, `\r`, `\t`.

Позиции считают Unicode code points в исходной декодированной строке, сохраняющей CRLF: offsets начинаются с нуля, end исключается, line/column начинаются с единицы. Это единая convention frontend → diagnostics → IR.

Независимые ожидания и фактические отчёты находятся в [conformance](../conformance/README.md). Исторический срез уже добавлен; request contexts требуют отдельного этапа и собственных проверок.
