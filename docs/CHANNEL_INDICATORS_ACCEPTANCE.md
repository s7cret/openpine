# Приёмка каналов волатильности R6

[Исходник 4295861dc305](https://github.com/s7cret/openpine/commit/4295861dc3054e37d6067ba740da96401ab433e5) ·
[Проверка ветки](https://github.com/s7cret/openpine/actions/runs/43383655650) ·
[Приёмка по тегу](https://github.com/s7cret/openpine/actions/runs/43383655932) ·
[Машиночитаемое свидетельство](evidence/channel-indicators-20261113.json)

Python 3.14.8, CI 57/57, один wheel.
На Windows прошли 2018 тестов. На Linux прошли 2017 тестов.
На Linux пропущена проверка коротких путей Windows 8.3.

Все 4609 прежних наблюдений conformance
сохранены. Добавлены 12 расчётных сценариев и 18 ожидаемых отказов.
Отчёты Windows и Linux совпадают. На установленном wheel обеих ОС
прогон через HTTP восстановил состояние окна в середине открытой свечи
и записал следующее оповещение с подставленной шириной полос Боллинджера.

На Linux в economics запрос start для flat v1 получил HTTP 503 COORDINATOR_BUSY при занятом слоте prepare. Сервер пометил отказ как повторяемый. Повтор использовал тот же SHA и общий wheel.
[Первый журнал](https://github.com/s7cret/openpine/actions/runs/43383655932/job/131756399281) · [Успешный повтор](https://github.com/s7cret/openpine/actions/runs/43383655932/job/131802585479).

Фиксированный bb проверен в Pine v1–v6. Новые контракты рассчитаны
на v4–v6. Сравнение с исполнением TradingView остаётся отдельным этапом.
[Профиль](CHANNEL_INDICATORS.md).
