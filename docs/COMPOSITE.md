# Составные результаты и индикаторы v1–v6

Выпуск `pine-composite-20261023.1` закрепляет профили
`openpine.composite.v1.1` … `v6.1`: плоские результаты из нескольких значений,
variance/stdev, MACD и Bollinger Bands. Это модель по локальным версионным
артефактам; точное исполнение в TradingView пока не использовалось как эталон.

## Версии

| API | v1–v2 | v3–v4 | v5–v6 |
| --- | --- | --- | --- |
| Variance/stdev | `variance(x,y)`, `stdev(x,y)`, positional | `variance(source,length)`, `stdev(source,length)` | `ta.variance(source,length,biased=true)`, `ta.stdev(source,length,biased=true)` |
| MACD | `macd(source,fastlen,slowlen,siglen)`, positional | То же, named допустимы | `ta.macd(source,fastlen,slowlen,siglen)` |
| BB | `bb(series,length,mult)`, реконструкция, positional | v3 — реконструкция; v4 имеет запись `bb` | `ta.bb(series,length,mult)` |

MACD записан во всех шести каталогах. Legacy result `array` в v1/v2
интерпретирован как фиксированные три результата; это не поддержка массивов.
BB отсутствует в каталогах v1–v3 и явно реконструирован по общей формуле.
Evidence и статус каждой API находятся в новом bundle. Прежние bundles сохранены.

Длины — `const/input/simple int` от 1 до 100000, фиксированные для вызова.
Series-length перегрузки справочника не входят в этот профиль. `biased`
доступен только в v5/v6 и может быть series bool. Неопределённый biased
возвращает `na`, сохраняя обновление окна.

## Несколько результатов

```pine
//@version=6
indicator("Composite results")
momentum(src, fast, slow, signal) =>
    [line, average, histogram] = ta.macd(src, fast, slow, signal)
    [line, average, histogram]
[line, _, histogram] = momentum(close, 3, 8, 3)
plot(line)
plot(histogram)
```

Плоский результат содержит 2…16 scalar int/float/bool/string. Элементы
получают самый сильный qualifier зависимостей. Вызов вычисляется один раз,
элементы — слева направо. Распаковка объявляет новые scalar-переменные после
вычисления результата. `_` можно повторять: значение вычисляется и
отбрасывается; читать его как объявленную переменную нельзя.

UDF и value-if с явным else возвращают такие же результаты. Ветки имеют
одинаковую арность и совместимые поэлементные типы; int/float приводятся к
float. Каждая написанная цепочка вызовов имеет свои состояния. История
распакованных переменных использует прежние правила физических баров.

Исключены коллекции, вложенные tuples, tuple-параметры, хранение tuple в
одной переменной, tuple-переприсваивание и tuple-тернарный оператор. Bare `na`
требует разрешённого scalar-типа, например `float(na)`.

## Числовая модель и состояние

Окно содержит последние `length` определённых источников; до полного окна
результат — `na`. Пропуск не сдвигает окно, готовое значение сохраняется.
Population variance делит сумму квадратов отклонений на `length`, unbiased —
на `length-1`. Unbiased при длине 1 даёт `na`; population даёт 0.

Двоичные суммы и квадраты накапливаются точно в целых числах ограниченной
разрядности. Окно хранит `length` значений и обновляется без полного пересчёта.
Это сохраняет малую дисперсию при большом смещении цены. Stdev вычисляется
без промежуточного float-overflow variance; сам variance за пределами
finite float завершается диагностикой.

MACD использует существующие EMA: SMA seed в v1–v4, первое определённое
значение в v5/v6. Signal — EMA от определённых значений fast−slow;
histogram — MACD−signal. При пропуске исходника fast/slow держат состояние,
а signal продолжает получать их определённую разность.
BB возвращает среднее и среднее ± mult × population stdev.

Общий бюджет — 1000000 ячеек; legacy seed MACD учитывается в нём. Checkpoint
сохраняет все части составного вызова. Окно проверяется по листьям и точным
суммам, seed — по форме и счётчикам. Preview использует overlays изменённых
ячеек и не меняет подтверждённые значения.

Typed IR `.7` добавляет TupleIR/UnpackIR и типизированные продукты.
Scalar-программы сохраняют формы `.5`/`.6`; runtime-state `.3` используется
для нового IR. Codec и admission проверяют весь артефакт до эффектов.
Программы и checkpoints другой реализации требуют явной перекомпиляции.

## Запрошенные контексты

```pine
[higherClose, higherAverage] = request.security("TEST:TECHNICAL", "4", [close, ta.ema(close, 3)])
[higherBasis, higherUpper, higherLower] = request.security("TEST:TECHNICAL", "4", ta.bb(close, 3, 0.8))
```

Выпуск `pine-requests-20261023.4` добавляет flat numeric tuple до 16 элементов.
Запрос имеет одно частное вычисление, курсор, событие доступности и checkpoint.
Gaps применяются сразу ко всем компонентам; пропуск выдаёт соответствующее
количество `na`. Компоненты относятся к одному доступному старшему бару.
Лимиты 128 узлов/16 уровней, явные снимки и same/higher timeframe сохраняются.
UDF/captures внутри запросов остаются вне профиля.

В v1/v2 historical lookahead on показывает финальный результат старшего бара
при его открытии. Примеры отмечают это явно. С v3 default — off. Observed
replay использует наблюдённые события и независимый rollback.

## Примеры и исследования

[examples/composite](../examples/composite/) содержит 12 стратегий:
`macd-cross-vN.pine` и `bb-reversion-vN.pine`. Данные — собственные 240 минутных
и 60 четырёхминутных свечей; хеши записаны в `fixtures.json`. MACD входит при
line > signal и подтверждении старшей EMA, выходит при пересечении вниз.
BB входит при пересечении нижней полосы и нахождении ниже старшего среднего;
выход — к среднему. Защита использует цены сигнального close.

```sh
openpine job --source examples/composite/macd-cross-v6.pine --bars examples/composite/bars.json --contexts examples/composite/contexts.json --state-dir .runtime/composite
openpine job --source examples/composite/bb-reversion-v6.pine --bars examples/composite/bars.json --contexts examples/composite/contexts.json --state-dir .runtime/composite
```

Планы `macd-experiment.json`, `bb-search.json` и `macd-walkforward.json`
используют существующие API исследований. При отправке серверу снимки
регистрируются в его каталоге; к плану добавляются `primary_context` и
`contexts` с серверным snapshot_id, как в [контракте контекстов](CONTEXTS.md).
`tools.installed_composite_smoke` проверяет CLI/HTTP, паузу, график, журнал,
отдельный holdout и хеши отчётов.

Проверки: [план приёмки](COMPOSITE_PLAN.md), 72 независимых сценария
`conformance/composite`, тесты `test_composite*`, инструменты
`tools.composite_soak` и `tools.composite_growth`. Результаты квалификации
фиксируются отдельно от наличия реализации и fixtures.
