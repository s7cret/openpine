# Приёмка оповещений защитных заявок R6

[Исходник 08eb6f7a04e0](https://github.com/s7cret/openpine/commit/08eb6f7a04e011b15818be5889b967afea7904a1) · [Веточная проверка](https://github.com/s7cret/openpine/actions/runs/43180090080) · [Приёмка по тегу](https://github.com/s7cret/openpine/actions/runs/43180090009)

SHA 08eb6f7a04e0. CI 57/57, Python 3.14.8. В реестре каждой ОС 2 000 тестов. Conformance охватывает 4 583 сценариев, корпус — 180 программ.

- Для `strategy.order()`, `strategy.close_all()` и `strategy.exit()` проверены исполнения Pine v1–v6. Общий текст доступен с v4. Подавление и сообщения тейк-профита, стоп-лосса и трейлинг-стопа доступны с v5.
- Проверены частичное и полное закрытие, замена, отмена, OCA и последовательность `order_fill` и `alert()`. Сделки и капитал совпали с контрольным replay.
- На общем wheel в Windows и Linux сравнили HTTP-журнал непрерывного replay и replay с перезапусками до и после защитного исполнения. Подавление алерта сохранило сделку в торговом журнале.
- На Windows прошли 2 000 тестов, на Linux — 1 999. На Linux пропущена проверка коротких путей Windows 8.3. Подтверждены четыре отчёта восстановления.
- Из 4 579 прежних наблюдений 4 573 совпали. В четырёх архивных случаях уточнена диагностика. Две проверки отказа v5–v6 заменены сценариями успешного исполнения. Добавлены ещё четыре проверки границ. Отчёты conformance совпадают между Windows и Linux.

Первая приёмка выявила ошибку при разборе параметров оформления в заголовке стратегии. Сценарий `v6-strategy-label` прошёл после исправления. [Первая приёмка](https://github.com/s7cret/openpine/actions/runs/43178423236)

Windows-группа lots получила JOB_TIMEOUT при возобновлении conditional v2 на 382-й свече. [Первый журнал](https://github.com/s7cret/openpine/actions/runs/43180090009/job/131061761211) · [Повтор](https://github.com/s7cret/openpine/actions/runs/43180090009/job/131116851148)

Windows-группа economics достигла лимита ожидания корпуса 1200 секунд. Пройдено 19 из 20 сценариев. Выполнялся bands-routing-v6. [Первый журнал](https://github.com/s7cret/openpine/actions/runs/43180090009/job/131061763321) · [Повтор](https://github.com/s7cret/openpine/actions/runs/43180090009/job/131116851223)

На локальной Windows группа lots прошла на том же wheel с исходными лимитами.

На локальной Windows корпус economics прошёл за 623 секунды на том же wheel.

Сверка с исполнением TradingView проводится отдельно.
[Профиль](ALERTS.md) · [Модель исполнения](adr/0049-protective-fill-alerts.md) · [Свидетельства](evidence/protective-fill-alerts-20261112.json)
