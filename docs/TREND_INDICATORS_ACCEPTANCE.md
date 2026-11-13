# Приёмка DMI, Supertrend и Parabolic SAR в R6

[Исходник ad1f3d776e76](https://github.com/s7cret/openpine/commit/ad1f3d776e7605222fa2a8a913456c9a5479d44f) ·
[Проверка ветки](https://github.com/s7cret/openpine/actions/runs/43407895017) ·
[Приёмка по тегу](https://github.com/s7cret/openpine/actions/runs/43407895048) ·
[Машиночитаемое свидетельство](evidence/trend-indicators-20261113.json)

Python 3.14.8, CI 57/57, один wheel.
На Windows прошли 2025 тестов. На Linux прошли 2024 тестов.
На Linux пропущена проверка коротких путей Windows 8.3.

Все 4639 прежних наблюдений conformance
сохранены. Добавлены 12 расчётных сценариев и 9 ожидаемых отказов.
Отчёты Windows и Linux совпадают. На установленном wheel обеих ОС
прогон через HTTP восстановил replay перед разворотом Supertrend.
Журнал HTTP содержал ожидаемые подстановки ADX. В тестах
восстановленный replay совпал с непрерывным по рядам и оповещениям.

На первом проходе Windows-сценарий lots остановился на 300 из 360 свечей при восстановлении fifo v2: воркер исчерпал лимит 120 секунд. Локально этот прогон завершился за 31,6 секунды после возобновления. Повторён только этот сценарий с тем же wheel и лимитом.
[Первый журнал](https://github.com/s7cret/openpine/actions/runs/43407895048/job/131835176535) · [Успешный повтор](https://github.com/s7cret/openpine/actions/runs/43407895048/job/131886862574).

SAR проверен в Pine v1–v6, DMI и Supertrend в v4–v6.
Сравнение с исполнением TradingView остаётся отдельным этапом.
[Профиль](TREND_INDICATORS.md).
