# Цели запроса относительно основного контекста

Чистые UDF в целях добавляет отдельный [function-targets профиль](FUNCTION_TARGETS.md).
Ограничения ниже описывают прежний primary профиль и сохраняются для его контрактов.

`openpine.primary-targets.1` дополняет вычисляемые цели отдельным контрактом.
Один сохранённый IR компилируется без рынка и выполняется на выбранном основном
инструменте и интервале. Перед чтением свечей вычисляется конкретный граф.

```pine
//@version=6
indicator("Primary context")
sym=input.symbol("")
tf=input.timeframe("", options=["", "1", "2"])
plot(request.security(sym, tf, close))
```

Также разрешены `syminfo.tickerid` и `timeframe.period` v4–v6; v1–v3 используют
`tickerid` и `period`. Ссылки сохраняют qualifier `simple`. Разрешены только эти
две ссылки и прежние ограниченные const/input выражения. Другие simple, series,
мутации, var/varip, локальные значения и UDF в целях остаются за пределами профиля.
Лимиты: 128 узлов и глубина 16 на цель, до 16 запросов и 8 контекстов.

Пустой результат цели наследует соответствующее поле основного контекста.
Пустые `input.symbol`/`input.timeframe` доступны с v5; раньше используются
generic string inputs. Raw settings сохраняют пустую строку. Ограничения options
применяются к raw значению: при options пустая строка должна присутствовать явно.
Новые определения используют `openpine.primary-inputs.v5.1`/`v6.1`; сохранённые
`openpine.target-inputs.vN.1` по-прежнему требуют непустые значения.

Матрица источников — `spec/primary-targets-evidence.json`: пустой интервал описан
в артефактах начиная с v3, пустой символ — с v5. Для более ранних версий это
обозначенная реконструкция исторического профиля. Точное сравнение с TradingView
остаётся отдельным этапом. Импортированный bundle и прежние профили не изменены.

## Подготовка и воспроизводимость

`POST /api/launches/requirements` принимает `input_ref`, `inputs` и опциональный
`primary_context` — полный payload существующего MarketContext. При отсутствии
необходимых метаданных возвращает `status: needs_primary`, список требуемых полей,
шаблоны зависимостей, пустые contexts и null context_graph. Метаданные разрешают
цели без загрузки свечей и создания заданий. Неверное уже известное значение
отклоняется, даже если другое поле ещё ожидает основного контекста.

UI получает метаданные из выбранного сохранённого снимка или формы провайдера.
Пустые typed inputs показывают наследование; эффективный инструмент и интервал
отображаются отдельно. Смена снимка сбрасывает ранее загруженные основные данные.
Перед запуском сервер повторно получает метаданные из реального источника.

CLI использует ту же функцию планирования:
`python -m apps.openpine requirements --artifact program.json` возвращает
needs_primary; `--primary-context metadata.json` разрешает цели без истории.
`--source script.pine` и `--inputs overrides.json` также поддерживаются.

Граф фиксирует logical symbol/timeframe/timezone/session/time_rule, raw targets,
полный набор зависимостей и SHA настроек. Context ID, provenance и ревизии данных
закреплены существующими RunPlan bindings. Переданные primary metadata должны
совпадать со снимком; восстановление проверяет прежние идентичности. Workers,
эксперименты, search/holdout, walk-forward, replay и live используют общий resolver.
Совпадающий запрос использует основной feed без дополнительной подписки.

## Приёмка

`python -m tools.primary_targets_spec --check` проверяет воспроизводимость
профиля и 38 независимых наблюдений (32 исполнения, 6 ожидаемых отказов).
`python -m tools.primary_targets_flow --directory NEW_DIR` прогоняет сохранённый
IR, UDF/SMA/state, worker pause/resume, restart replay и свежий offline replay.
`tools.installed_primary_targets_smoke` входит в suite research на обеих ОС:
CLI использует один IR на TEST:A/1 и TEST:B/2 для всех шести версий; HTTP проверяет
ожидание метаданных, resolved graph и запуск без дополнительных источников.
`python -m tools.primary_targets_soak --directory NEW_DIR` прогоняет миллион
событий с независимой формулой для каждого значения и restart посреди младшего бара.
