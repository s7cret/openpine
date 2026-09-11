# Этап 2 — единая численная матрица и native reference identity

Локальная разработка от `5b714812dd1cdbec89827ff512e8b9cfecf92f12`. Никакие remote
ветки, refs, PR или Actions этим проходом не меняются. Весь этап 2 не принят.

## Реальное исправление, найденное сквозным корпусом

Прежняя ветка numeric-neighbor поиска сравнивала raw descriptor только с
`array<int>` / `array<float>`. Обычные скомпилированные array-конструкторы законно
создавали raw descriptor `int` / `float`. Поэтому root-тест с вручную созданным
массивом проходил, а Pine-программа возвращала `-1` для поиска соседнего индекса.
Новый corpus воспроизвёл 160 отказов на четырёх v5 формах left/right × namespace/
method через пять путей исполнения в full/compact.

Исправлен **общий владелец типов**: RuntimeReferenceHeap предоставляет проверенную
каноническую проекцию дескриптора. Её используют проверки language binding,
номинальных полей и numeric search. Raw storage, portable markers, типы элементов,
правила номинальной идентичности и checkpoint bytes не изменены; тип не угадывается
по первому значению. Это не алиас имени builtin в compiler и не новый evaluator.

Для отсортированного `[-2,0,1,5,9]` с query=3 результат left=2/right=3. Сквозная
стратегия отрабатывает прямой и импортированный вариант, namespace/method, v5/v6,
закрытие/следующее открытие и fill-recalculation. При корректных индексах отправляет
entry qty=3 на bar 2, price=102 на закрытии либо 103 на следующем открытии.
In-process transport harness не выдаётся за sandbox; четыре protected-worker
случая выделены отдельно и обязательны в будущем CI.

## Независимый численный корпус

`verification/builtin-numeric-closure-v1` содержит 58 Pine/data/settings/expected
случаев. `scripts/derive_numeric_closure.py --check` заново вычисляет **ожидаемые**
значения стандартной библиотекой Fraction и сравнивает байты, ничего не перезаписывая.
Он не импортирует parser, compiler, runtime или observed results.

| Семейство | Случаев | Граница доказательства |
|---|---:|---|
| RSI v3/v4 | 6 | Mixed gain/loss, lengths 2/3/5, отдельная RMA bootstrap |
| MACD v3/v4 | 6 | Рациональные EMA/SMA-seeded signal recurrences, разные длины |
| Binary search v5 | 22 | namespace/method; finite int/float/fractional/large, duplicates для first/last, interior misses |
| min/max v5/v6 | 16 | 2/3/6 аргументов, отрицательные значения и NA |
| TSI v5/v6 | 8 | Только продолжение после 12 одинаковых ненулевых приращений; startup/NA не принимаются |

58 × 5 paths × 2 transcript modes = 580 исполнительных вариантов, плюс две
проверки целостности. Пять paths: ABI, compiled historical, realtime, abort/rollback,
JSON checkpoint. Результат ABI не используется как expected для compiled path.
В каждом варианте проверяются фактические binding/type/target identities.

**Ограничения не скрыты.** v1/v2 RSI/MACD не получают expected из более позднего
справочника. Доступность/absent-neighbor поведение binary search в v4 остаются
неподтверждёнными: 11 новых provisional v4 случаев были исключены до фиксации
корпуса после проверки источников, но ни одна прежняя строка каталога или тест
не удалены. Их исходный проект и неудачный диагностический запуск сохранены в
поставке. TSI conditioning — видимое ограничение горизонта, не способ выдать
непроверенную начальную семантику за совместимость. Все значения engineering,
`tradingview_verified=false`.

## Один индекс вместо разрозненных PASS

`openpine.verification.evidence_index` не реализует математические формулы и не
исполняет Pine. Используется существующий corpus/comparison owner.

1. Фиксируются **2374** идентичности version/symbol/overload/call-form, contract
   hashes и шесть каталогов. Это установленный каталог, не официальный exhaustive
   Pine denominator. Added/removed/changed rows требуют явного review.
2. План фиксирует 12 корпусов, exact content hashes, 100 обязательных group/mode/
   path запусков и отдельные locks назначений case → signature. Нельзя получить
   зелёный индекс удалением группы, пути или назначения.
3. Входы читаются только из явно указанных evidence roots. JSON duplicate keys,
   nonfinite values, oversized inputs, traversal и symlink paths отклоняются.
4. Из raw observations/assignments и frozen expected заново вычисляется report;
   sealed `PASS` сам по себе не считается результатом. Старые catalog/target или
   producer/compiler/runtime source pins не получают credit текущего комплекта.
5. Повторные одинаковые файлы и порядок roots не меняют знаменатель. Failed и
   passed наблюдение одного случая дают конфликт, не выбор «лучшего» прогона.
6. Неатрибутированные случаи прежних array/string corpus явно остаются в отчёте.
   EXAMPLES_ALL_PATHS означает только проверенные ограниченные примеры, отдельно
   от qualifier-domain compatibility и полноты численного контракта.

Hashes доказывают согласованность bytes, **не авторство** и не факт запуска против
недобросовестного автора, меняющего сразу source/expected/report/locks. Для release
необходима доверенная цепочка CI и точный source checkout. Индекс не подменяет
sandbox, full stage gate или внешний TradingView execution oracle.

## Активный остаток

`verification/stage2-remaining-matrix.json` и отдельный hash lock содержат 16
конкретных рабочих пакетов под четырьмя исходными критериями. У каждого указан
owner, существующие evidence paths и проверяемая граница завершения. Сохранены
исходные восемь OP-номеров и SHA ТЗ. Это **группы остатка**, не 16 равноценных
семантических сценариев; отношение количества пакетов не является готовностью.

`stage2-remaining` соединяет эту матрицу с индексом именно текущих source pins.
В нём отдельно перечислены direct rows без пяти путей и сигнатуры с проверкой
только после conditioning. Он не переводит ни один широкий критерий в accepted.

В `stage2-progress.json` старые summary/pending поля методов сверены с уже
проверенным `methods-merge-20260911.json`. Исторические записи сохранены явно;
новый локальный numeric/index блок не получает CI старого опубликованного source.

## Команды

```sh
python scripts/derive_numeric_closure.py --check
python -m openpine.verification builtin-index --host-root . --evidence /path/to/current/evidence \
  --surface-lock verification/stage2-callable-lock.json \
  --plan verification/stage2-evidence-plan.json \
  --expected-plan-hash sha256:b5bcf397cf49d2f0702b59e0218b8861c88134e71e5f756ae3d0134feceb943c \
  --output /path/to/current/evidence/builtin-index.json
python -m openpine.verification stage2-remaining --host-root . \
  --builtin-index /path/to/current/evidence/builtin-index.json --output stage2-remaining.json
```

В постоянный workflow добавлен replay после неизменённых functional/build gates.
Все прежние selected regressions, workers и inventory checks сохранены. Workflow
изменён **только в локальном кандидате**, не запущен и не опубликован.

## Поставка и границы проверки

Точные локальные коммиты и результаты — отдельный publication-local receipt после
завершения тестов. Локальные тесты Python 3.13 не означают новый Linux CI на 3.11/3.13.
Численные корпуса не подтверждают весь broker/request/visual слой. Части, уже
назначенные этапам 3–7, отмечаются владельцами, но не исчезают из общего каталога.
Формат ABI/checkpoint не мигрирован, скорость всего бэктеста не измерена.

Семантические первоисточники (не execution oracle):
- https://www.tradingview.com/pine-script-docs/language/arrays/
- https://www.tradingview.com/pine-script-reference/v5/
- https://fr.tradingview.com/pine-script-reference/v3/
- https://in.tradingview.com/pine-script-reference/v4/
- https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-5/
- https://www.tradingview.com/support/solutions/43000592290-true-strength-index/

## Новый объединённый gate обнаружил старую неподтверждённую область

Строгий replay **не стал зелёным**: 90 из 100 group/path сочетаний совпадают целиком,
10 вариантов string corpus сохраняют три несовпадения v5 str.tonumber для
`"1_000"`, `"1e2"`, `" 1 "`. В старых корпусах ожидания для них уже имели
UNVERIFIED authority и не участвовали в accepted assignments. Тот же результат
обнаруживается в исходном CI-архиве baseline: это не новый runtime regression.
У ещё двух v5 специальных строк результат совпадает, но authority также не принята.

Это **расхождение с неподтверждёнными ожиданиями**, не три доказанных бага PineLib.
Общий справочник v5 говорит о корректно сформированном int/float, но не разрешает
автоматически перенести подробные лексические правила текущего руководства назад.
Не менялись ни старая string-семантика, ни expected ради зелёного цвета.
`unassigned_outcomes` сохраняет first divergence и пометку authority. Новый gate
останавливает приёмку до явного разрешения этой области, не скрывая её под общим
количеством прошедших pytest. Обе выходные сводки сохраняются даже при nonzero exit.

Фактический численный охват bounded examples растёт с 270 до 284 из 303 direct-строк,
но из 284 две TSI-строки имеют только conditioning horizon. Это не доля готовности
Stage 2, не 284 полностью доказанных контракта и не принятая приёмка всего корпуса.
