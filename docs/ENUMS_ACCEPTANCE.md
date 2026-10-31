# Приёмка enums и input.enum v5/v6

Исходник [`f519f59`](https://github.com/s7cret/openpine/commit/f519f59978d2b7bf1068dffcd2029f3a308c904b)
прошёл [CI 57/57](https://github.com/s7cret/openpine/actions/runs/41154505897) на Python **3.14.8**, попытка 1.
[Правила и границы](ENUMS.md), [машинное свидетельство](evidence/enums-20261031.json).

| Проверка | Результат |
| --- | --- |
| Unit/integration | 1547 test ID в 168 модулях на каждой ОС; все прежние 1519 ID сохранены, добавлено 28 |
| Платформенный skip | Windows: 1547 passed; Linux: 1546 passed и штатный skip Windows 8.3 filesystem alias |
| Conformance на каждой ОС | 2783 случая: 1799 исполнений и 984 ожидаемых отказа |
| Прежние наблюдения | Все 2701 сохранены точно, кроме program identity; переходов ожиданий нет |
| Новый conformance | 82 случая: 43 исполнения и 39 отказов; полные отчёты одинаковы между ОС |
| Корпус целых программ | 104 записи: 98 исполнений и 6 отказов; прежние 96 записей сохранены полностью |
| Сравнения корпуса | 704 passed; 48 явных различий моделей not_equivalent; полное переносимое свидетельство одинаково между ОС |
| Установленный пакет | Один общий wheel, 18 suites на каждой ОС и четыре проверки восстановления |

Проверены nominal typing, UDF/defaults/overloads/methods, enum-поля UDT,
scalar enum arrays, input schemas и замороженные overrides, CLI/HTTP,
пресеты, эксперименты и search candidates. Every-bar/every-event restore
сохраняет enum history, array IDs и varip; обычные поля UDT откатываются.
Подмена metadata input, чужие типы и невозможные member tokens отвергаются.
100 тысяч enum slots восстановлены через durable records в прежнем бюджете
512 МиБ, включая незакрытый бар и отсутствие дальнейшего IO из checkpoint.
64 scalar varip enum проверены при бюджете 16 МиБ без резервирования максимальной
пользовательской строки на каждый закрытый token. Лимиты не увеличены.

Отдельный локальный Windows-контроль прошёл три программы прежних таймаутов:
OCA netting v1, pending stop v2 и lots conditional v2. Использованы реальный HTTP,
подготовленный запуск, воркер, пауза/resume, checkpoint каждый бар и прежний
worker timeout 120 секунд. Результаты сверены с независимыми оракулами;
времена фаз сохранены в машинном свидетельстве.

Браузерный harness проверил пять сценариев production JS с тестовым API:
подписи и nominal tokens, пресет, reset, enum search axes и сохранённые строки
эксперимента. Реальный HTTP/worker путь проверяется отдельным integration test;
браузерный harness не является проверкой полного live-сервера.

Ограниченный профиль исключает enum-dependent requests, enum tuples,
maps/matrices и import/export. Для v5 явно описано противоречие simple/const
в первичных описаниях; default input принимает прямой member. Сравнение с
TradingView не заявляется. Точные границы — в [профиле](ENUMS.md).

Source identity: `c85d970c15f2a19801ab6ac3da781319c9a278864dd89fb0a0b59cbd4ec52cf6`.
Compiler/runtime fingerprint: `868c01e93d5fcc99ee1783888130dd4eeffd64f2b4a3544fea63bc6acf831532`.
Общий wheel: `50ea9d6eaa4bae8ab48244cb9130dac51a4af6c71cb410ffd5f57a142a85555c`.
После приёмки меняются только документация и свидетельства.

Первый кандидат `430e4e2` прошёл спецификацию на обеих ОС, но один тест
HTTP-каталога ожидал прежние 96 программ вместо 104. Прогон остановлен;
в трёх тестах корпуса/HTTP/паспорта обновлены явные ожидания до 104 записей,
98 компиляций, шести отказов и batches 64+40. Идентичность compiler/runtime
сохранена. Для нового коммита выполнен отдельный полный CI; исходная причина
и контроль изменений записаны в `previous_candidate` машинного свидетельства.
