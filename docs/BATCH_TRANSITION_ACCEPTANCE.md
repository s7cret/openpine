# Приёмка пакетного режима R6

[Исходник 0658b080f4e7](https://github.com/s7cret/openpine/commit/0658b080f4e7593b73dcd80159e83335643a5f57) · [Push в ветку](https://github.com/s7cret/openpine/actions/runs/42357825707) · [Приёмка по тегу](https://github.com/s7cret/openpine/actions/runs/42357944919)

CI 57/57, Python 3.14.8, 1888 тестов в реестре каждой ОС, 4154 наблюдения conformance, 180 программ корпуса.

- Веточный push запустил одно задание. Быстрый контур за 38.2 с проверил контракты,
  статическую матрицу и сценарий [polylines](../conformance/polylines/v5-force-overlay-time.pine).
- Тег `r6-accept/batch-pilot-20261107` указал на тот же SHA. Windows и Linux использовали общий wheel
  `cbed35b3673d467ac307fdda9dadfb8603d239747311b7e79e89abb2a6505ce1`. Проверены все 57 архивов, включая четыре сценария восстановления
  и длительных проверок.
- На Windows прошли 1888 тестов, на Linux — 1887.
  Проверка Windows 8.3 на Linux ожидаемо пропущена.
- Добавлены два исполнения v5–v6 с полилинией по времени и принудительным размещением.
  Сохранены все 4152 прежних наблюдений.
  66 отчётов совпали побайтно. Обновлён только [отчёт polylines](../conformance/results/polylines.json).
- Матрица содержит 2046 строк по перегрузкам. В ней 4 строки FULL,
  1049 PARTIAL и 993 MISSING. Сверка с TradingView ведётся отдельно.
- Переносимые свидетельства 180 программ совпали с прежней приёмкой и между ОС.
  Сохранены 62 явно отмеченных различия моделей.

Windows exits остановился с JOB_TIMEOUT на 259 из 400 баров при восстановлении stop v2 с бара 3. Heartbeat обновлялся. Нехватка памяти или диска не зафиксирована. Лимит остался 120 секунд. [Журнал](https://github.com/s7cret/openpine/actions/runs/42357944919/job/128364942952) · [Успешный повтор](https://github.com/s7cret/openpine/actions/runs/42357944919/job/128387736628)

[Режим разработки](DEVELOPMENT_BATCHES.md) · [Матрица функций](PINE_FUNCTION_COVERAGE.md) · [Запись приёмки](evidence/batch-transition-20261107.json)
