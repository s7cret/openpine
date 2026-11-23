# Итоговая приёмка OpenPine R6

Этапы 1–5 приняты по [предыдущему выпуску](R6_MULTIMARKET_ACCEPTANCE.md). Для этапов 6–25 приведены четыре прогона одного wheel на Windows и Ubuntu с Python 3.14.8. [Сценарии всех четырёх блоков](R6_EXPANDED_SCENARIOS.md)

| Этапы | Исходник | Тесты Windows / Ubuntu | Сценарии спецификации на ОС | Выполненные задания CI |
| --- | --- | ---: | ---: | ---: |
| 6–10 | [ea4835cb](evidence/expanded-block-1-acceptance.json) | 2283 / 2282 | 5543 | [57/57](https://github.com/s7cret/openpine/actions/runs/45159800706) |
| 11–15 | [8f648c47](evidence/expanded-block-2-acceptance.json) | 2293 / 2292 | 5543 | [57/57](https://github.com/s7cret/openpine/actions/runs/45159806416) |
| 16–20 | [7cce109c](evidence/expanded-block-3-acceptance.json) | 2298 / 2297 | 5543 | [57/57](https://github.com/s7cret/openpine/actions/runs/45159811879) |
| 21–25 | [09914cfe](evidence/expanded-block-4-acceptance.json) | 2299 / 2298 | 5543 | [57/57](https://github.com/s7cret/openpine/actions/runs/45159817946) |

В первом прогоне исправленного блока 11–15 проверка Windows numeric достигла лимита ожидания в 1 200 секунд со статусом корпуса running. Результат повторной попытки относится к тому же коммиту и wheel. В истории приведены ссылки на задания предыдущих попыток.

Портфель и эксперимент, экспортированные в GitHub Actions на Ubuntu, повторены на локальной Windows. [Результат и хеши архивов GitHub](evidence/expanded-runner-transfer.json)

На 100 тысячах, миллионе и 10 миллионах свечей совпали все 111 блоков выходных рядов Windows и Ubuntu. На Ubuntu исполнитель обработал 10 млн свечей за 191,783 с. [Измерения и повторное вычисление SHA блоков](evidence/expanded-linux-capacity.json)

На баре 2 CLI и браузер показали 31 и 32 для двух вариантов выражения. После перезапуска сохранилась карточка исполнения на строке 6. [Свидетельства браузерных сценариев](evidence/expanded-browser-workflows.json)

Ubuntu пропускает проверку коротких путей Windows. Каждый блок сохранил все 185 программ каталога и точные позиции шести ожидаемых отказов. [История девяти запусков](evidence/expanded-ci-history.json)

Обратный перенос эксперимента и двух стратегий выполнен на wheel итогового тега. [Проверка Linux → Windows](evidence/expanded-final-transfer.json) · [Измерения и восстановление очереди](EXPANDED_STAGE24.md)
