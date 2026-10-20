# Исторические бары и EMA

```pine
//@version=6
indicator("EMA on fixed bars")
plot(ta.ema(close, 3), "ema")
```

```sh
python -m apps.openpine run --source conformance/historical-bars/ema.pine --bars conformance/historical-bars/fixed.json
python -m apps.openpine compile --source conformance/historical-bars/ema.pine --profile historical
```

`--bars` выбирает implementation profile `openpine.historical-bars.1`. Для компиляции без данных профиль выбирается явно через `--profile historical`. Без этих параметров сохраняется literal-профиль с одним синтетическим наблюдением. `run --profile historical` без данных возвращает `INPUT_UNAVAILABLE/BARS_REQUIRED`.

Четыре fixture-бара с close 10, 20, 30, 40 дают `result.outputs[0].values=[10.0,15.0,22.5,31.25]` и `observation_count=4`. `plot(close)` возвращает исходные close. Каждая выходная серия содержит по одному элементу на бар, а её ID определяется местом вызова `plot`.

## Входные данные

```json
{
  "schema_version": "openpine.historical-bars.1",
  "context_id": "example.fixed",
  "symbol": "TEST:OWN",
  "timeframe": "1",
  "bars": [
    {"time": 0, "close": 10.0},
    {"time": 60000, "close": 20.0}
  ]
}
```

Это локальный close-only контракт для уже подтверждённых исторических баров. `time` — время открытия в UTC миллисекундах, неотрицательное 64-битное целое, строго возрастающее. Пропуски времени допустимы и не создают дополнительных баров. `symbol` и `timeframe` идентифицируют данные; ресемплинг и проверка биржевого календаря не выполняются. OHLCV передаётся отдельным форматом `.2`, описанным в [SERIES.md](SERIES.md). Неизвестные поля, realtime flags и requested contexts отклоняются.

Close должен быть конечным числом или `null`. Входные int нормализуются в float, bool отвергается. `null` обозначает числовое Pine `na`; выход также сохраняет его как JSON `null`, без NaN/Infinity. Дубликаты JSON-ключей, пустые данные, повторные/обратные времена и нечисловые close дают `INPUT_INVALID/BARS_INVALID`. Лимиты реализации: 8 MiB входного JSON и 100 000 баров; это не лимиты платформы Pine.

В `result.data` записываются SHA-256 нормализованного входного контракта, context ID, symbol, timeframe и исполненные времена. Hash включает все значения close и метаданные. Изменение данных меняет hash результата; тот же исходник и профиль компилируются в тот же IR независимо от набора баров. Schema результата — `openpine.run-result.2`.

## Исполнение и состояние

Compiler разрешает `close` в `ReferenceIR` со стабильным symbol/assertion ID, hash контракта и типом `series float`. Runtime проверяет эти сведения вместе со всем IR до первого эффекта. Числовое преобразование int → float представлено явным `ConvertIR`; bool в него не попадает.

`execution/session` передаёт по одному `BarFrame` в хронологическом порядке. Runtime видит текущий close и своё накопленное состояние, без доступа к будущим барам или исходному JSON. Декларация `indicator` обрабатывается один раз, тело — один раз на исторический бар, как в [модели исполнения Pine](https://www.tradingview.com/pine-script-docs/language/execution-model/).

Для v5/v6 `ta.ema(source, length)` требует числовой float source (int допускает явное promotion) и int length с qualifier не сильнее simple. Результат всегда `series float`. По закреплённому `rule.ema` этих профилей первый доступный source служит seed, коэффициент равен `2/(length+1)`, последующие значения равны `alpha*source + (1-alpha)*previous`. До первого source результат — `na`; пропуск после seed сохраняет предыдущую EMA. Условие `length > 0` проверяется при исполнении согласно `rule.ema.length`; нарушение даёт `RUNTIME_ERROR/EMA_LENGTH` с позицией вызова.

Для v1–v4 операция `ema` использует отдельный kernel с SMA seed после `length` доступных source. В v1/v2 позиционные параметры называются `x,y`, в v3/v4 — `source,length`. Подробности источников, непроверенного исторического NA-поведения и реконструкции — в [VERSIONS.md](VERSIONS.md).

Состояние принадлежит одному запуску и ключу `(call_site, context_id)`. Два написанных вызова EMA независимы; вложенные вызовы вычисляются изнутри наружу. Новый запуск, в том числе через тот же `Session`, начинает с пустого состояния. Ошибка внутри бара прерывает оценивание; частичный результат не публикуется. Realtime rollback и checkpoint/resume пока не реализованы.

При выборе версионного каталога значения необходимых принятых правил сравниваются с формами, реализованными kernel. Изменение seed, NA-policy или другого требуемого правила не принимается молча. Исходный снимок v6 не изменялся; v1–v5 закреплены отдельным version-review release.

## Версии и границы

Исполнение текущего среза доступно для v1–v6. Каждая версия имеет собственные symbol/overload/parameter/assertion IDs, semantic profile/hash и диагностическую версию. `study/abs/ema` v1–v4 и `indicator/math.abs/ta.ema` v5/v6 не являются взаимозаменяемыми alias. У старых профилей есть явные оговорки, доступные в `identity.compatibility_notes`; автоматической подстановки семантики v6 нет.

Поддерживаются глобальные вызовы, [переменные, присваивания, арифметика, условия и var](STATEMENTS.md). `open/high/low/volume`, `bar_index`, индексирование истории и новые числовые/TA функции реализованы в [профиле рядов](SERIES.md). Requests и realtime остаются следующими этапами. `plot` сохраняет ограниченный harness-контракт и не возвращает графический handle.

Фактическое покрытие и независимые ожидания описаны в [conformance](../conformance/README.md). Общий gate на Python 3.14.8: `python -m tools.release_gate`.
