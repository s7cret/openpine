# Приёмка str.split и array.join в Pine v4–v6

Исходник `e09b906c6ed8bdf8f7553c052d526caf4c5f83c9` прошёл 57 из 57 заданий CI. Windows и Ubuntu использовали один wheel на Python 3.14.8 с SHA-256 `0629277b4f1cb587624596365adef866f4329b4e50c9787df1ded3fa656ed583`. Аудит проверил происхождение и SHA-256 всех 62 архивов и воспроизвёл итог приёмки с проверками восстановления. [GitHub Actions](https://github.com/s7cret/openpine/actions/runs/45402914292)

| Проверка | Windows | Ubuntu |
| --- | ---: | ---: |
| Пройденные модульные тесты | 2 308 | 2 307 |
| Пропуск проверки коротких путей Windows 8.3 | 0 | 1 |
| Сценарии conformance | 5 551 | 5 551 |
| Наборы проверок установленного wheel | 18 | 18 |

Три новых скрипта проверяют сумму 19.5, изменение элемента через ссылку, точный результат объединения и независимость повторного разбиения на восьми барах. Разделитель меняется с одного символа на два. После восстановления из checkpoint результат мутации ссылки совпал с непрерывным исполнением во всех трёх версиях. [Скрипт v4](../conformance/scalar-targets/v4-split-join.pine) · [v5](../conformance/scalar-targets/v5-split-join.pine) · [v6](../conformance/scalar-targets/v6-split-join.pine) · [Проверки checkpoint](../tests/test_arrays_state.py)

Все 5 548 прежних наблюдений сохранились относительно принятого исходника `6a067db9`. Отчёты conformance двух ОС совпали. Шесть записей функций перешли из MISSING в PARTIAL. Для общего типа array в описании v4 поддерживается тот же набор string/int/float, что в v5/v6. [Результаты корпуса](../conformance/results/scalar-targets.json) · [Контракт и границы профиля](../docs/ARRAY_TEXT.md)
