# Приёмка оповещений об исполнении заявок R6

[Исходник 83e58fb91930](https://github.com/s7cret/openpine/commit/83e58fb91930b6322f2a69d1311eff239cb6497c) · [Веточная проверка](https://github.com/s7cret/openpine/actions/runs/43094526293) · [Приёмка по тегу](https://github.com/s7cret/openpine/actions/runs/43094522680)

SHA 83e58fb91930. CI 57/57, Python 3.14.8. В реестре каждой ОС 1 994 тестов. Conformance охватывает 4 579 сценариев, корпус — 180 программ.

- Для `strategy.entry()` и `strategy.close()` проверены исполнения Pine v1–v6. В v4–v6 текст использует данные наблюдения исполнения, в v5–v6 флаг подавления сохраняется при создании заявки.
- Проверены замена и отмена отложенного входа, возобновление до и после исполнения, идентификаторы торгового журнала и порядок с вызовами `alert()` в цикле расчёта.
- HTTP-сценарий чтения журнала и возобновления прошёл на общем wheel в Windows и Linux. Заявка с подавленным алертом сохранила исполнение в торговом журнале.
- На Windows прошли 1 994 тестов, на Linux — 1 993. На Linux пропущена проверка коротких путей Windows 8.3. Подтверждены четыре отчёта восстановления.
- Сохранены 4 574 прежних наблюдений, добавлено 5 сценариев. Отчёты conformance совпадают между Windows и Linux.

Восстановление unified прошло. Сервис артефактов ответил 404 на финализацию отчёта. [Первый журнал](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130780029287) · [Повтор](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130860610432)

Проверка спецификации Windows прошла. Сервис артефактов вернул HTML при создании отчёта. [Первый журнал](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130780029290) · [Повтор](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130860611440)

Проверка спецификации Linux прошла. Сервис артефактов вернул HTML при создании отчёта. [Первый журнал](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130780029292) · [Повтор](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130860652473)

Воркеры проверки store получили 404 при запросе ссылки на уже собранный wheel. [Первый журнал](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130780029368) · [Повтор](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130860611359)

Восстановление managed прошло. Сервис артефактов вернул HTML при создании отчёта. [Первый журнал](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130780029470) · [Повтор](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130860680920)

Windows-группа observed достигла прежнего лимита ожидания 1200 секунд. Пройдено 19 из 20 сценариев, последний выполнялся на момент остановки. [Первый журнал](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130805412876) · [Повтор](https://github.com/s7cret/openpine/actions/runs/43094522680/job/130860614797)

Быстрая проверка ветки прошла. Сервис артефактов вернул HTML при создании отчёта. [Первый журнал](https://github.com/s7cret/openpine/actions/runs/43094526293/job/130779819640) · [Повтор](https://github.com/s7cret/openpine/actions/runs/43094526293)

[Инцидент GitHub Actions](https://stspg.io/jdjlq5zjyl48)

На локальной Windows с тем же wheel корпус observed прошёл за 618 секунд.

Сверка с исполнением TradingView проводится отдельно.
[Профиль](ALERTS.md) · [Модель исполнения](adr/0048-strategy-fill-alerts.md) · [Свидетельства](evidence/strategy-fill-alerts-20261111.json)
