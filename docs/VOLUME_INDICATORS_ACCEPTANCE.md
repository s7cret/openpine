# Приёмка объёмных индикаторов и VWAP в R6

[Исходник 3795835e523a](https://github.com/s7cret/openpine/commit/3795835e523aec219b61fb12266e29c784a14e4a) ·
[Проверка ветки](https://github.com/s7cret/openpine/actions/runs/43472986386) ·
[Приёмка по тегу](https://github.com/s7cret/openpine/actions/runs/43472986212) ·
[Машиночитаемое свидетельство](evidence/volume-indicators-20261113.json)

Python 3.14.8, CI 57/57, один wheel.
На Windows прошли 2040 тестов. На Linux прошли 2039 тестов.
На Linux пропущена проверка коротких путей Windows 8.3.

Все 4690 прежних наблюдений conformance
сохранены. Добавлены 16 расчётных сценариев и 8 ожидаемых отказов.
Отчёты Windows и Linux совпадают. На установленном wheel обеих ОС
журнал HTTP с подстановкой MFI и ряды графика совпали у непрерывного
replay и прогона, восстановленного перед сбросом якоря.

Переменные accdist и VWAP проверены в Pine v1–v6, OBV, PVT и MFI
в v4–v6, VWAP с явным якорем и полосами в v5–v6.
Сравнение с исполнением TradingView остаётся отдельным этапом.
[Профиль](VOLUME_INDICATORS.md).
