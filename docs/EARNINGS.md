# Прибыль на акцию в OpenPine R6

В закреплённом справочнике Pine v4 функция `earnings()` поддерживает `earnings.actual` и `earnings.estimate`. В v5/v6 используется `request.earnings()`, также доступно `earnings.standardized`. Поле по умолчанию — actual, параметры gaps и lookahead выключены, неизвестный рынок вызывает ошибку. В v5/v6 валюта по умолчанию берётся из сведений об инструменте, при отсутствии этих сведений используется валюта потока. Для фиксированного рынка можно выбрать исходную валюту или пересчитать EPS по дневному курсу закреплённой пары. [Пример](CURRENCY_RATES.md). В v1–v3 эти запросы недоступны. Прогноз в примере поступает из поля estimate. Переменные earnings.future_* находятся за границей профиля.

```powershell
python -m apps.openpine job --source examples/earnings/indicator.pine --bars conformance/earnings/bars.json --corporate-actions conformance/earnings/events.json --plan-only
python -m apps.openpine job --source examples/earnings/strategy.pine --bars conformance/earnings/bars.json --corporate-actions conformance/earnings/events.json
```

HTTP принимает тот же файл через `POST /api/corporate-actions`. При создании задания или replay передайте полученный SHA-256 в `corporate_snapshot_id`. Для исправленного файла каталог возвращает другой идентификатор. Пауза и восстановление сохраняют исходную привязку. [Данные](../conformance/earnings/events.json) · [Правила ревизий](adr/0080-earnings.md).

| Бар | Прогноз | Факт | Факт минус прогноз | Стандартизированный EPS |
| --- | ---: | ---: | ---: | ---: |
| 0–2 | −1 | na | na | na |
| 3 | −1 | 0 | 1 | na |
| 4–5 | −1 | 0 | 1 | 0,25 |
| 6 | −1 | −0,25 | 0,75 | 0,25 |
| 7 | −1 | −0,25 | 0,75 | na |

[Индикатор](../examples/earnings/indicator.pine) показывает прогноз, факт и разницу. [Стратегия](../examples/earnings/strategy.pine) подаёт заявку на баре 3 после публикации нулевого EPS, исполнение приходится на открытие бара 4 по цене 104. На баре 6 приходит уточнение. Отдельный тест переносит его в промежуток перед следующей сессией.

### Нагрузка на Windows, Python 3.14.8

`python -m tools.corporate_scale --earnings` читает снимок один раз и делает один проход по свечам. RSS измеряется выборочно во время исполнения. Размер checkpoint относится к потоковому приёмнику результатов.

| Свечей | Публикаций | Секунды | Пиковый наблюдавшийся RSS, байт | Checkpoint, байт |
| ---: | ---: | ---: | ---: | ---: |
| 100 000 | 8 | 3,799 | 96 079 872 | 479 |
| 100 000 | 8 000 | 3,630 | 99 246 080 | 479 |
| 1 000 000 | 8 000 | 35,169 | 100 179 968 | 481 |
