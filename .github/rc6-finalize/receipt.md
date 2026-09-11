# Stage 2 — проверка сохранённого рекурсивного/методного комплекта

Дата: 11 сентября 2026. Это приёмка ограниченного комплекта, **не завершение всего этапа 2**.

## Исполненные исходники и проверки

Исполнен host `8aa6a3e4f6f172652daf3ba37aade9f21e122c6c`, tree `2a7869613ed508f86e25b202a1302cc5e3164b6a`, с точными pins:

- Pine2AST `9820fe4f28d01d32d8a13f328517d9d7a5779497`;
- PineLib `97902943bd8f1300c3bb3e3e314465ac92fa057a`;
- Ast2Python `caadb136a52dcb49cea7bb838e8a0d5d2724cf30`;
- остальные четыре библиотеки не изменены относительно прежнего принятого набора.

Run https://github.com/s7cret/openpine/actions/runs/34594834647 реально выполнил все восемь согласованных наборов на Python 3.11/3.13. Скачанные XML пересчитаны независимо. На каждом интерпретаторе: Contracts 557, Pine2AST 2910, PineLib 4902, Ast2Python 1145, Engine 1102, Optimizer 281, Provider 601, OpenPine 8761; всего **20 259**, zero failures/errors/skips. Все 16 787 прежних ID сохранены; добавлено 3 472. Точные node-id hashes и байты исходных архивов совпали между интерпретаторами. Все восемь wheel/sdist build прошли; исходные tracked-файлы чистые.

Frontend того же исходника: 152 Vitest, 22 Node, production build и API checks. Защищённые worker, AppArmor и Bubblewrap сохранены. Provider исключает только прежние пять внешних сетевых случаев. Host — native плюс закреплённая affected-path выборка, не полный проект. Число 20 259 включает инфраструктурные проверки, а не столько же внешних подтверждений Pine.

## Разделение исполнения и итоговой метаинформации

Исходный run был диагностическим: он не переписывал committed inventory и не утверждал release автоматически. После независимой проверки его proposed inventory закрепляется отдельным метакоммитом. Финальный gate повторно исполняет неизменённый stage1 агрегатор на реальных execution receipts и observations, а также метаданные/архитектурные регрессии и существующий lint/build шаг. Он использует проверенные source pins и явно проверяет отсутствие изменений runtime, tests, corpus expectations и постоянного workflow относительно 8aa6a3e4.

20 259 функциональных случаев относятся к исходнику 8aa6a3e4, не к воображаемому повторному полному запуску документационного SHA. Итоговые commit/tree и integration refs фиксируются отдельным readback. Одноразовые review/export/finalize workflows не входят в итоговое release-дерево.

## Независимая проверка изменений

20 PRIMARY_FORMULA_DERIVATION рядов recursive TA пересчитаны только stdlib Fraction/Decimal, без импортов project/SUT и без подстановки observed результатов. Все рациональные ожидания совпали. 13 исходных UNVERIFIED строк остаются исключёнными из положительных назначений, но сохранены в исходном знаменателе.

Raw target полностью воспроизведён из принятого manifest только тремя заменами series -> simple: ta.tsi.short_length, ta.tsi.long_length, ta.valuewhen.occurrence. Производный content_hash: `sha256:3bbb194318736ff97aff6a50ef4a3c4238fda3191f3971ab2e5fe9e14e27c2f7`. Изменения старых remainder/file checksums независимо выведены из этого преобразования. Единственное изменённое прежнее численное ожидание — EMA(1,2,3,4,5; length=3) с first-source seed: хвост 9/4,25/8,65/16. Остальные старые проверки и numeric map/string fixtures не ослаблялись. Каталоги v1–v4 побайтно совпадают; существующие parser/compiler tests не изменены.

Локальные supplementary проверки: 103 parser и 38 target случаев успешны. Полный локальный compiler: 1143 PASS, два отказа isolated Python -I из-за отсутствующей установленной зависимости Pine2AST. В настоящем Linux CI с установленными пакетами все 1145 прошли. Локальные случаи пересекаются с общим набором и повторно не прибавляются.

## Остатки не закрываются количеством тестов

Общий normalized target binding пока сохраняет имена параметров, но теряет qualifier ceilings. Исполненные normal-source отрицательные проверки enforced producer-слоем; универсальная source/target qualifier certification остаётся открытой. TSI NA/warmup, исторические профили, B/C exported/imported methods, полный каталог и независимые ожидания всей builtin-поверхности требуют дальнейшей работы. Full Stage 2 и tradingview_verified остаются false.

Репозитории не обновляются по одинаковой строке package version: нужен весь RC6_LIFECYCLE_SOURCES.json и перекомпиляция артефактов. Изменение runtime policy/ABI не считается совместимым со старым checkpoint автоматически.

## Сохранение веток и доказательств

Теги `archive/20260911/stage2/language-completion-20260907` и `archive/20260911/stage2/nominal-ci-20260907` указывают на точные прежние tips. Удалены только эти две старые ветки; main и исторические releases не изменялись. Активный кандидат сохраняется до guarded integration.

Root artifacts: Python3.11 `10262094742` SHA256 `f9ab85c1df37ef9a7abdcf3da7826b851c7b84f5518faaae662dfabcb404e1db`; Python3.13 `10262289682` SHA256 `76830236e405d939369ee2c79ae625058904cab942d0b80b7edb15afbce71a4b`; frontend `10262139728` SHA256 `89331097fd7483a15c4daa9a80606f1565b7702cbdb4063dc8863eafe8cb03c5`. Исходные stage1 expectations не менялись. Измеренного ускорения не заявляется.
