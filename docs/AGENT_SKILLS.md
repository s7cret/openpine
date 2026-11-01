# Навыки Codex для OpenPine

Четыре навыка хранятся в этом репозитории, в `.agents/skills/`, и доступны
для задач OpenPine. Они не устанавливают Superpowers/ECC целиком и не меняют
пользовательские настройки Codex. Автоматический выбор оставлен включённым;
описания ограничивают каждый навык своей задачей.

| Навык | Когда применять | Основа |
| --- | --- | --- |
| [openpine-debugging](../.agents/skills/openpine-debugging/SKILL.md) | Конкретная ошибка, сбой проверки, расхождение restore/realtime или ОС | Адаптация systematic-debugging |
| [openpine-verification](../.agents/skills/openpine-verification/SKILL.md) | Сопоставить заявление о готовности с проверками и публикацией | Адаптация verification-before-completion |
| [openpine-adr](../.agents/skills/openpine-adr/SKILL.md) | Сохранить принятое архитектурное решение или объяснить существующее | Адаптация architecture-decision-records |
| [openpine-acceptance](../.agents/skills/openpine-acceptance/SKILL.md) | Выполнить или проверить приёмку R6 и опубликовать её свидетельства | Собственный навык OpenPine |

Явный вызов — `$openpine-debugging`, `$openpine-verification`,
`$openpine-adr` или `$openpine-acceptance`. В обычной работе Codex может выбрать
навык по его описанию. Навыки доступны со следующего сообщения после обнаружения
файлов; если клиент ещё не обновил список, откройте проект заново.

## Что сохранено и что адаптировано

Все три сторонних `SKILL.md` переработаны: исходные файлы целиком не установлены.
Без изменений перенесены их MIT-уведомления. Шаблон ADR и последовательности
отладки/проверки основаны на закреплённых источниках ниже.

| Источник | Сохранено | Адаптация для OpenPine |
| --- | --- | --- |
| Superpowers systematic-debugging | Воспроизведение → поиск причины → проверка гипотезы → исправление | Привязка к Binder/IR/runtime/storage, native/resume, alias/varip, лимитам и различиям ОС; удалены обязательная зависимость от TDD-навыка и правило «три попытки доказывают неправильную архитектуру» |
| Superpowers verification-before-completion | Доказательство каждого существенного заявления, проверка результата и diff | Повторное использование подходящих свидетельств по fingerprint; полный прогон не требуется на каждое сообщение; docs-only коммит отделён от коммита с CI |
| ECC architecture-decision-records | Краткий ADR: контекст, решение, альтернативы, последствия, жизненный цикл | Учтены версии Pine и интерпретации источников; запись уже разрешённого решения не требует повторного согласования создания каталога/файла |

Не перенесены bootstrap `using-superpowers`, hooks, автозагрузка всех навыков,
непрерывное обучение/память, автоматическое делегирование, обязательные worktree,
Claude-команды и универсальные npm/pytest-проверки. Эти навыки не требуют новых
runtime-зависимостей и не создают отдельный release gate.

Собственный `openpine-acceptance` использует существующие `tools.release_gate`,
`tools.ci_artifacts`, `tools.install_check` и `tools.recovery_gate`. Его
[карта команд](../.agents/skills/openpine-acceptance/references/commands.md)
различает локальную проверку, fast feedback, полный native gate и приёмку CI.
[Правила свидетельств](../.agents/skills/openpine-acceptance/references/evidence.md)
покрывают сохранность прежних ID, неизменяемые bundles, общий wheel, попытки CI,
восстановление и обновление матрицы. Число тестов/заданий не зафиксировано в
инструкциях: оно берётся из текущих инструментов.

## Происхождение

Проверенные ревизии, 2026-11-01:

- Superpowers: [2ce8aa15d05bd57213e6f746f953aafb7ded7a8b](https://github.com/obra/superpowers/commit/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b).
  [systematic-debugging](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/systematic-debugging/SKILL.md),
  [verification-before-completion](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/verification-before-completion/SKILL.md).
  Copyright (c) 2025 Jesse Vincent; [сохранённая MIT-лицензия](../.agents/skills/openpine-debugging/LICENSE).
- ECC: [b38be6a7f6350964d59328b52f0624e1d1d552d1](https://github.com/affaan-m/ECC/commit/b38be6a7f6350964d59328b52f0624e1d1d552d1).
  [architecture-decision-records](https://github.com/affaan-m/ECC/blob/b38be6a7f6350964d59328b52f0624e1d1d552d1/skills/architecture-decision-records/SKILL.md).
  Copyright (c) 2026 Affaan Mustafa; [сохранённая MIT-лицензия](../.agents/skills/openpine-adr/LICENSE).

Для нового заимствования или обновления сначала сравните закреплённый источник
и локальную адаптацию. Внешние изменения не подхватываются автоматически.
Собственный `openpine-acceptance` распространяется по MIT OpenPine.

## Область обнаружения

Codex читает навыки из `.agents/skills/` текущего рабочего каталога и каталогов
до корня Git-репозитория. Это поддерживается
[официальной документацией](https://learn.chatgpt.com/docs/build-skills#where-codex-loads-local-skills).

Основное расположение — `openpine-r6/.agents/skills/`. В текущем локальном
workspace чат запускается из родительской папки `OpenPine`. Для неё созданы
четыре локальные directory junction в `OpenPine/.agents/skills/`, указывающие
на эти же каталоги в `openpine-r6`. Содержимое не дублируется. Это локальные
ссылки данного workspace, а не установка в `~/.codex/skills` или
`~/.agents/skills`. После обычного клонирования достаточно открыть сам
Git-репозиторий; родительские ссылки не нужны.

## Проверка изменений навыков

Проверяйте frontmatter, область срабатывания, локальные ссылки и соответствие
команд текущим CLI. Проиграйте подходящие запросы: обычный план не должен
запускать приёмку, status-запрос — новый полный прогон, а существующее разрешение
на запись ADR не должно превращаться в повторный вопрос о создании файла.

Текущий application fingerprint не включает `.agents/`, `AGENTS.md` и docs.
Поэтому его неизменность подтверждает сохранение квалифицированного приложения,
но не качество новых инструкций. Эти изменения проверяются отдельно.
Полные application suites для одной правки навыков не требуются.

Четыре навыка проверены официальным валидатором skill-creator на Python 3.14.8;
локальные ссылки и пять CLI проверены отдельно. Семь типовых запросов разобраны
вручную по инструкциям; это не независимый live-тест выбора навыков моделью.
Существующие application receipts повторно прошли аудит с неизменным source
fingerprint; полный набор тестов приложения заново не запускался.
[Свидетельство проверки](evidence/project-skills-20261101.json).

Решение об этой установке: [ADR-0001](adr/0001-project-scoped-codex-skills.md).
