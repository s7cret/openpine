# Время исполнения `timenow`

OpenPine поддерживает `timenow` как `series int` в Pine v3–v6.
Значение выражено в миллисекундах Unix. В Pine v1–v2 действует
диагностика `ENVIRONMENT_VERSION`.
[Руководство Pine v3](https://www.tradingview.com/pine-script-docs/v3/essential/sessions-and-time-functions/#built-in-variables-for-working-with-time) ·
[Семантика `timenow`](https://www.tradingview.com/pine-script-docs/concepts/time/#timenow).

Исторический запуск принимает `--execution-time-ms` в CLI `run` и `job`
или `execution_time_ms` в JSON запроса `/api/launches` или `/api/jobs`.
Все исторические свечи и запрошенные контексты используют одну метку.
Она входит в сохранённый план задания и привязку checkpoint к окружению.
При чтении `timenow` без метки исторический запуск выдаёт
`INPUT_UNAVAILABLE / EXECUTION_TIME_REQUIRED`.

```sh
python -m apps.openpine run --source examples/execution-time/update-gaps.pine --bars conformance/execution-time/bars.json --execution-time-ms 1709208180789
```

Replay использует `event_time` записи. На запоздавшем закрытии это время
получения закрытия. Запрошенное выражение использует событие своего
контекста, основной расчёт использует событие, которое его вызвало.
Повтор уже обработанной записи сохраняет результат. Для исторического
префикса replay одна метка задаётся в `environments[primary_id].execution_time_ms`
и применяется ко всем контекстам. Live-прогрев закрепляет время провайдера
при создании сессии. `{{timenow}}` в шаблоне оповещения выводит время
того же события в UTC с точностью до секунды.

[Пример с `varip`](../examples/execution-time/update-gaps.pine) измеряет
интервал между обновлениями свечи и публикует оповещение на закрытии.
[Сценарии](../conformance/execution-time/cases.json) · [ADR 0075](adr/0075-execution-time.md).

Статус `PARTIAL` отражает принятую детерминированную модель OpenPine.
Сравнение с исполнением TradingView ведётся отдельно.
