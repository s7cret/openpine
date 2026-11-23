# Приёмка пяти этапов OpenPine R6

| Этап | Исходник | Сценарии спецификации на ОС | Прошли на Windows | Прошли на Ubuntu | GitHub Actions |
| --- | --- | ---: | ---: | ---: | --- |
| 1. [Динамические и вложенные запросы](../docs/DYNAMIC_REQUESTS_ACCEPTANCE.md) | [`232b0a8d`](https://github.com/s7cret/openpine/commit/232b0a8dd4bce4408cc67a7f7ad07ff233a7bc4b) | 5543 | 2265 | 2264 | [57/57](https://github.com/s7cret/openpine/actions/runs/45070620301) |
| 2. [Календари, покрытие и исправления данных](MARKET_SCHEDULES_ACCEPTANCE.md) | [`d5d7533a`](https://github.com/s7cret/openpine/commit/d5d7533a0bfc8a09070f4348887168a49228f8f7) | 5543 | 2269 | 2268 | [57/57](https://github.com/s7cret/openpine/actions/runs/45115966305) |
| 3. [История и состояние многорыночного индикатора](STATEFUL_MARKETS_ACCEPTANCE.md) | [`54ea4d35`](https://github.com/s7cret/openpine/commit/54ea4d35f07ca2b4702380da851c5ae93912bc7d) | 5543 | 2275 | 2274 | [57/57](https://github.com/s7cret/openpine/actions/runs/45088044803) |
| 4. [Заявки, исполнения и валютный сигнал](MULTIMARKET_ORDERS_ACCEPTANCE.md) | [`b8dacd69`](https://github.com/s7cret/openpine/commit/b8dacd6920c72bd725d605e3959776731e814793) | 5543 | 2276 | 2275 | [57/57](https://github.com/s7cret/openpine/actions/runs/45088933548) |
| 5. [Локальное рабочее место и восстановление](MULTIMARKET_WORKSPACE_ACCEPTANCE.md) | [`157f7be8`](https://github.com/s7cret/openpine/commit/157f7be8d8da9465ffc0e1e4ed19cc30cc8c27e9) | 5543 | 2277 | 2276 | [57/57](https://github.com/s7cret/openpine/actions/runs/45116578142) |

В каждом прогоне Ubuntu пропущен один тест коротких путей Windows 8.3.

В полном пользовательском сценарии ночной рынок передаёт сигнал в USD. На основном инструменте с котировкой в EUR проверены шесть исполнений и три закрытые сделки. При начальном капитале 1000 EUR итоговый капитал равен 995,48 EUR. Сохранённый повтор использует те же свечи и курс. После восстановления хранилища отложенный стоп-ордер даёт прежний журнал.

Отмена через браузер проверена при двух одновременно открытых курсорах Parquet. В отменённом задании результат отсутствует, второе завершилось с шестью исполнениями. Измерения времени, памяти и чтений на 100 тыс. и 1 млн баров опубликованы вместе с условиями воспроизведения. [Локальный отчёт](../docs/MULTIMARKET_WORKSPACE_LOCAL.md)

Сценарии проверяют поддержку закреплённых артефактов Pine v1–v6 и численных эталонов OpenPine. Сверка с TradingView предусмотрена отдельно. Дальнейшие этапы 6–25 описаны в [расширенном плане](../docs/R6_EXPANDED_GOAL.md).

## Границы проверенного поведения

- [Когда пересчитывается запрошенное значение и в какой валюте исполняется заявка](../docs/adr/0084-security-currency.md)
- [Как вложенный запрос наследует рынок и интервал](../docs/adr/0085-nested-request-clock.md)
- [Какие локальные массивы и пользовательские типы поддерживает выражение запроса](../docs/CONTEXT_GRAPH.md)
- [Pine-файл стратегии, команда CLI и эталонные исполнения](../docs/ECONOMICS.md)
