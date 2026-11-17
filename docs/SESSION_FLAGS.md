# Флаги торговой сессии инструмента

| Переменная | Pine | Профиль UTC 24×7 |
| --- | --- | --- |
| `session.ismarket` | v4–v6 | `true` на каждом баре |
| `session.ispremarket`, `session.ispostmarket` | v4–v6 | `false` на каждом баре |
| `session.isfirstbar`, `session.isfirstbar_regular` | v5–v6 | Первая полученная свеча UTC-дня |
| `session.islastbar`, `session.islastbar_regular` | v5–v6 | Свеча, чьё календарное закрытие совпадает со следующей UTC-полуночью |

Все семь переменных имеют тип `series bool`. Флаги состояния рынка
работают и на D/W/M. В непрерывном профиле весь UTC-день считается
регулярной сессией, поэтому обычные и регулярные флаги границ совпадают.
[Состояния рынка](https://www.tradingview.com/pine-script-docs/concepts/sessions/#market-states).

Для флагов границ поддерживаются D и минутные интервалы, которые делят
1440 без остатка. На D каждая полученная свеча отмечает обе границы.
На W, M и интервалах вроде 7 минут чтение флага границы выдаёт
`PROFILE_UNCOVERED / SESSION_FLAG_TIMEFRAME`. В ранних версиях Pine
для недоступных переменных возвращается `ENVIRONMENT_VERSION`.

Если снимок начинается с середины дня или первая минута пропущена,
первая доступная свеча этого дня получает `session.isfirstbar`.
Если свеча с закрытием в полночь пропущена, у дня нет отметки
`session.islastbar`. Окончание снимка и пауза replay не создают её.
Все ревизии одной свечи видят одни и те же флаги. Для фиксации значения
на закрытии используется также `barstate.isconfirmed`. Запрос
`request.security()` вычисляет флаги в собственном контексте.
[Границы в Pine](https://www.tradingview.com/pine-script-docs/concepts/sessions/#first-and-last-bars).

[Индикатор дневного объёма](../examples/session-flags/daily-volume.pine)
сбрасывает накопление на первой свече и публикует сумму на подтверждённом
закрытии последней. [Разреженный пример](../conformance/session-flags/sparse.json)
даёт значения 5 и 30 для двух дней с полученным закрытием в полночь.
[Корпус](../conformance/session-flags/cases.json) · [ADR 0074](adr/0074-session-flags.md).

Покрытие `PARTIAL` ограничено календарём UTC 24×7. Сверка исполнения
с TradingView относится к отдельному этапу.
