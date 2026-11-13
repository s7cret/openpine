# Приёмка dev, CCI, CMO, TSI и Williams %R в R6

[Исходник 278250f8bd54](https://github.com/s7cret/openpine/commit/278250f8bd542bcd3d608e456d167354420926f6) ·
[Проверка ветки](https://github.com/s7cret/openpine/actions/runs/43440218542) ·
[Приёмка по тегу](https://github.com/s7cret/openpine/actions/runs/43440218489) ·
[Машиночитаемое свидетельство](evidence/momentum-20261113.json)

Python 3.14.8, CI 57/57, один wheel.
На Windows прошли 2033 тестов. На Linux прошли 2032 тестов.
На Linux пропущена проверка коротких путей Windows 8.3.

Все 4660 прежних наблюдений conformance
сохранены. Добавлены 15 расчётных сценариев и 15 ожидаемых отказов.
Отчёты Windows и Linux совпадают. На установленном wheel обеих ОС
журнал HTTP с двумя подстановками CCI совпал у непрерывного replay
и прогона, восстановленного перед пересечением. В тестах также
сравнили ряды и итоговый checkpoint.

dev, CCI и TSI проверены в Pine v1–v6, CMO и Williams %R в v4–v6.
Сравнение с исполнением TradingView остаётся отдельным этапом.
[Профиль](MOMENTUM.md).
