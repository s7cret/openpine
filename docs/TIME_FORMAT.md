# Форматирование времени в OpenPine R6

В Pine v5–v6 доступен `str.format_time(time, format, timezone)`.
Вызов принимает Unix-миллисекунды и возвращает `series string`.
В Pine v1–v4 компилятор выдаёт `SIGNATURE_UNAVAILABLE`.
[История Pine](https://www.tradingview.com/pine-script-docs/release-notes/#october-2022).

Параметры `format` и `timezone` необязательны и принимают значения
`series string`. По умолчанию используется `yyyy-MM-dd'T'HH:mm:ssZ`
и связанная ссылка на `syminfo.timezone`. Текущий исторический профиль
OpenPine задаёт этому свойству значение UTC. В аргументе `time`
пропуск означает Unix-эпоху. Например, эпоха в Нью-Йорке выводится
как `1969-12-31T19:00:00-0500`.
[Правила Pine v5](https://www.tradingview.com/pine-script-docs/v5/concepts/time/#formatting-dates-and-times) ·
[Правила Pine v6](https://www.tradingview.com/pine-script-docs/concepts/time/#formatting-dates-and-times).

Маска поддерживает `yyyy`, `MM`, `dd`, `HH`, `mm`, `ss`, `SSS`, `Z`,
разделители и текст в апострофах. `SSS` выводит три цифры миллисекунд,
`Z` — смещение в виде `+HHmm` или `-HHmm`. Два соседних апострофа
выводят один. Результат и шаблон ограничены 4 096 кодовыми точками.
Операция расходует общий бюджет строковых операций бара.

Допустимы UTC, GMT, фиксированные смещения вроде `UTC-5`,
`UTC+05:30` и `GMT+0100`, а также имена и псевдонимы из IANA, например
`America/New_York`. Для смещений часы меньше 24, минуты меньше 60.
Имена IANA чувствительны к регистру. Данные берутся из закреплённого
пакета `tzdata==2026.6` с базой IANA `2026f`.
[Решение о данных поясов](adr/0068-time-format.md).
[Календарные функции и числовой timestamp с часовым поясом](CALENDAR_ZONES.md).

Форматирование принимает даты, представимые Python в UTC и выбранном
поясе в пределах 0001–9999. Unix-время до эпохи допустимо. Ошибки маски,
пояса и диапазона дают `TIME_FORMAT_PATTERN`, `TIME_FORMAT_ZONE` и
`TIME_FORMAT_RANGE`. Ошибка загрузки закреплённых данных даёт
`TIME_FORMAT_TZDATA`. Сверка исполнения с TradingView ведётся отдельно.

[Пример с меткой и оповещением на осеннем переводе часов](../examples/time-format/dst-alert.pine).
