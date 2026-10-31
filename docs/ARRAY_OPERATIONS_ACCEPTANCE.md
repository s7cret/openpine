# Приёмка расширенных операций массивов

Исходник [`bea0e2b`](https://github.com/s7cret/openpine/commit/bea0e2b551d4d9d100fd439e7aa0e3e5c0cd676d)
прошёл [CI 57/57](https://github.com/s7cret/openpine/actions/runs/41203767267) на Python **3.14.8**, попытка 1.
[Правила и границы](ARRAY_OPERATIONS.md), [машинное свидетельство](evidence/array-operations-20261031.json).

| Проверка | Результат |
| --- | --- |
| Unit/integration | 1573 test ID / 169 модулей на каждой ОС; все прежние 1547 ID сохранены, добавлено 26 |
| Платформенный skip | Windows: 1573 passed; Linux: 1572 passed и штатный skip Windows 8.3 filesystem alias |
| Conformance на каждой ОС | 2910 случаев: 1859 исполнений и 1051 ожидаемый отказ |
| Прежние наблюдения | Все 2783 сохранены точно, кроме program identity; переходов ожиданий нет |
| Новый conformance | 127 случаев: 60 исполнений и 67 отказов; полные отчёты одинаковы между ОС |
| Корпус | 112 записей: 106 исполнений и 6 отказов; прежние 104 записи сохранены полностью |
| Сравнения корпуса | 762 passed и 50 явных not_equivalent моделей; полное переносимое свидетельство одинаково между ОС |
| Установленный пакет | Один общий wheel, 18 suites на ОС, четыре проверки восстановления |

Добавлены `insert/remove/reverse/fill/includes/indexof/lastindexof/concat` для
v4–v6, instance-формы для v5/v6, примитивы и существующие enum/UDT-массивы.
V1–v3 сохраняют отказ на array API; контрольные routing-программы используют
scalar-аналог. Новые generic контейнеры и heap kinds не вводятся. Старый bundle
массивов и прежний methods profile сохранены побайтно.

Проверены порядок named arguments, однократное вычисление receiver/value,
динамический конец fill, alias результата concat, self-concat, nominal equality,
UDT identity, missing, отрицательные v6 индексы insert/remove и диапазоны fill.
Работают UDF, методы, сохранённый IR, CLI/HTTP, воркеры, history/replay,
every-bar/every-event restore, COW и varip структуры/полей.

Fault-проверки подтверждают отсутствие частичной публикации при ошибках
временной памяти, работы и отмены. Staging освобождается, прежние слоты,
владение и счётчики ссылок сохраняются. Проверена отмена после построения
буфера до commit. Unicode payload, logical cells, каскадный GC и shallow UDT
ссылки учитываются прежним ledger. Лимиты не увеличены.

Self-concat 50 тысяч одинаковых UDT-ссылок в 100 тысяч сохраняет одну запись и
один контейнер. Durable checkpoint проверен на границе бара и внутри активного
бара в v5/v6 при прежнем бюджете задания 512 МиБ и restore reserve. После
restore runtime не читает checkpoint storage; ordinary поля откатываются,
помеченные varip поля продолжают состояние.

Перед полным CI прошли локальные проверки корпуса, HTTP, паспорта и прежних
object arrays. Восемь новых программ дополнительно прошли 24 задания воркеров:
58 сравнений совпали, два различия моделей явно not_equivalent.

Поиск missing и равные fill-границы являются явно закреплёнными OpenPine
интерпретациями; generic-примитивы v4 наследуют прежнюю реконструкцию.
Внешнее сравнение с TradingView остаётся отдельным этапом. Slice, сортировки,
вложенные коллекции и новые request-expression types исключены.

Source identity: `6055be8db7b16c0c469cb036533dd118c492e21d2b8fa85bb96f8ca4ac1fc852`.
Compiler/runtime fingerprint: `065a29e719091f01e20f0bbb89305c8efa4815921a8305032ff1659c592e08a4`.
Общий wheel: `81c10c44f3f8e3778e5ffd6590aaa2ea132273eca607cc06bc02f85b623c047d`.
После приёмки меняются только документация и свидетельства.

Первый прогон CI 41202674057 остановлен после корректного отказа паспорта:
в чистом checkout оставались отчёты с прежним implementation fingerprint.
Обновлённые локальные conformance-отчёты опубликованы коммитом этой приёмки;
компилятор, runtime, тесты, ожидания и source identity при исправлении не менялись.
Новый прогон полностью проверил исправленный checkout.
