# Дивиденды и сплиты в OpenPine R6

| Pine | Запись в закреплённой спецификации | Профиль |
| --- | --- | --- |
| v1–v3 | Имена функций отсутствуют | Вызов отклоняется при компиляции |
| v4 | `dividends(ticker, field, gaps, lookahead, ignore_invalid_symbol)`, `splits(...)` | PARTIAL |
| v5–v6 | `request.dividends(ticker, field, gaps, lookahead, ignore_invalid_symbol, currency)`, `request.splits(ticker, field, gaps, lookahead, ignore_invalid_symbol)` | PARTIAL |

Обе функции возвращают `series float`. Для дивидендов поле по умолчанию — `dividends.gross`; также доступно `dividends.net`. Для сплитов явно выбирают `splits.numerator` или `splits.denominator`. Общие значения по умолчанию — `barmerge.gaps_off`, `barmerge.lookahead_off`, `ignore_invalid_symbol=false`. Тикер фиксируется до начала прогона. Допускаются литералы, свойства основного рынка, фиксированные входы и поддержанные конструкторы. Профиль охватывает безусловные вызовы верхнего уровня. [Закреплённый справочник](../spec/bundles/pine-spec-20261020.1/reference/v6.json).

Источник подтверждает покрытие отдельно по рынку и семейству в полуоткрытом диапазоне UTC `[start_ms,end_ms)`. Пустой покрытый поток даёт `na`. Для известного рынка с отсутствующим семейством или неполным диапазоном возвращается `ACTION_COVERAGE`. Неизвестный рынок даёт `ACTION_SYMBOL` либо `na` при `ignore_invalid_symbol=true`. Для выплаты в исходной валюте допускается явно указать эту валюту. При наличии валюты графика профиль учитывает её как значение по умолчанию для v5–v6. В офлайн-снимке без валюты графика используется валюта потока. Для пересчёта нужен отдельный источник FX, сейчас запуск возвращает `ACTION_CURRENCY`.

На минутных барах тестового примера первая выплата вступает в силу на минуте 1, сумма становится известна на минуте 2 плюс 5 секунд. При `lookahead_off` завершённая минута 2 уже содержит значение. При `lookahead_on` оно видно на открытии минуты 3. Параметр `gaps_on` выдаёт новое наблюдение один раз на первый подходящий бар. При `gaps_off` сохраняется последнее опубликованное значение, включая пропущенную сумму net. Правила границ закреплены как локальная семантика OpenPine. [ADR-0079](adr/0079-corporate-actions.md).

### Запуск примеров

Из корня репозитория на Python 3.14.8:

```powershell
python -m apps.openpine job --source examples/corporate-actions/indicator.pine --bars conformance/corporate-actions/bars.json --corporate-actions conformance/corporate-actions/events.json --plan-only
python -m apps.openpine job --source examples/corporate-actions/strategy.pine --bars conformance/corporate-actions/bars.json --corporate-actions conformance/corporate-actions/events.json
```

Через HTTP передайте JSON набора в `POST /api/corporate-actions`. Поле `sha256` ответа используется как `corporate_snapshot_id` при подготовке запуска или replay. При подготовленном запуске в `prepared.requirements.corporate_actions` перечислены рынок, семейство, поле, снимок и границы требуемого диапазона каждого запроса. План, checkpoint и replay связываются с содержимым снимка. При исправлении данных импорт создаёт новый идентификатор.

| Бар (с нуля) | Gross, перенос | Net, только событие | Числитель | Знаменатель |
| --- | --- | --- | --- | --- |
| 0–1 | na | na | na | na |
| 2 | 2 | 1,5 | na | na |
| 3–5 | 2 | na | 2 | 1 |
| 6–7 | 3 | na | 1 | 4 |

[Индикатор](../examples/corporate-actions/indicator.pine) строит все четыре ряда. [Стратегия](../examples/corporate-actions/strategy.pine) открывает заявку на баре 6 по обратному сплиту и исполняет её на баре 7 по рыночному open 107. В этом профиле денежные зачисления брокеру, изменение количества акций и корректировка OHLC остаются отдельными операциями. [Данные и ожидания](../conformance/corporate-actions/cases.json).

### Нагрузка

`python -m tools.corporate_scale` исполняет индикатор на потоке синтетических минутных баров. Один снимок событий загружается перед проходом. Замер Windows, Python 3.14.8. Рабочий набор процесса измерялся примерно раз в тысячу баров. Размер checkpoint относится к сериализованному состоянию вычисления с внешним получателем результатов.

| Свечей | Секунды | Максимальный наблюдавшийся RSS, байт | Checkpoint, байт | Проходов по свечам |
| ---: | ---: | ---: | ---: | ---: |
| 100 000 | 3,305 | 94 928 896 | 479 | 1 |
| 1 000 000 | 30,643 | 95 113 216 | 481 | 1 |
