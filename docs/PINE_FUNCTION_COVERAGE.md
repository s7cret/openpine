# Покрытие функций Pine в OpenPine

Статическая матрица исторического профиля. Источник строк — [закреплённый bundle](../spec/lock.json).
Номер перегрузки берётся из её ID внутри версии, `—` обозначает запись без отдельного номера.
`FULL` означает проверенный полный контракт по закреплённому описанию, `PARTIAL` — реализованный срез,
`MISSING` — отсутствие совпадающего допуска, `N/A` — отсутствие функции в закреплённой версии.
Актуальная приёмка и её SHA указаны в [матрице семантики](SEMANTIC_COVERAGE.md).
Сверка с исполнением TradingView учитывается отдельно.
Пометка «вызов через объект» указывает на [методную форму графической операции](METHODS.md#методы-графических-объектов).

| Pine | FULL | PARTIAL | MISSING |
| --- | ---: | ---: | ---: |
| v1 | 0 | 81 | 29 |
| v2 | 0 | 85 | 31 |
| v3 | 0 | 99 | 14 |
| v4 | 0 | 248 | 21 |
| v5 | 2 | 643 | 74 |
| v6 | 3 | 621 | 95 |

## Pine v1

| Функция | Перегрузка | Статус | Граница |
| --- | ---: | --- | --- |
| `abs` | — | PARTIAL | `numeric.abs` · тип x в источнике описан неполно |
| `acos` | — | PARTIAL | `numeric.acos` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `alma` | — | PARTIAL | `weighted.alma` · тип series в источнике описан неполно · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `asin` | — | PARTIAL | `numeric.asin` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `atan` | — | PARTIAL | `numeric.atan` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `atr` | — | MISSING | нет совпадающего допуска для перегрузки |
| `avg` | — | PARTIAL | `numeric.avg` · 2–6 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `barcolor` | — | PARTIAL | `markers.barcolor` · параметры вне профиля editable |
| `barssince` | — | PARTIAL | `signals.barssince` · тип condition в источнике описан неполно |
| `bgcolor` | — | PARTIAL | `markers.bgcolor` · параметры вне профиля editable |
| `cci` | — | PARTIAL | `momentum.cci` · тип x в источнике описан неполно · тип y в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ceil` | — | PARTIAL | `numeric.ceil` · тип x в источнике описан неполно |
| `change` | — | PARTIAL | `series.change` · тип x в источнике описан неполно · тип y в источнике описан неполно · динамическая длина в профиле OpenPine · [История версии](CHANGE_VALUEWHEN.md) |
| `cog` | — | PARTIAL | `weighted.cog` · тип x в источнике описан неполно · тип y в источнике описан неполно · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `correlation` | — | PARTIAL | `correlation.pearson` · тип x в источнике описан неполно · тип y в источнике описан неполно · окно определённых пар, квалификатор длины по версии · [Профиль](CORRELATION.md) |
| `cos` | — | PARTIAL | `numeric.cos` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `cross` | — | PARTIAL | `series.cross` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `crossover` | — | PARTIAL | `series.crossover` · параметры исторического источника восстановлены частично |
| `crossunder` | — | PARTIAL | `series.crossunder` · параметры исторического источника восстановлены частично |
| `cum` | — | MISSING | нет совпадающего допуска для перегрузки |
| `dayofmonth` | — | MISSING | нет совпадающего допуска для перегрузки |
| `dayofweek` | — | MISSING | нет совпадающего допуска для перегрузки |
| `dev` | — | PARTIAL | `momentum.dev` · тип x в источнике описан неполно · тип y в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ema` | — | PARTIAL | `series.ema.sma_seed` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `exp` | — | PARTIAL | `numeric.exp` · тип x в источнике описан неполно |
| `falling` | — | MISSING | нет совпадающего допуска для перегрузки |
| `fill` | — | PARTIAL | `bands.fill_hline` / `bands.fill_plot` · параметры вне профиля editable · тип title в источнике описан неполно · часть типов transp вне профиля · однотипные ссылки, первые три аргумента позиционные |
| `fixnan` | — | MISSING | нет совпадающего допуска для перегрузки |
| `floor` | — | PARTIAL | `numeric.floor` · тип x в источнике описан неполно |
| `heikenashi` | — | MISSING | нет совпадающего допуска для перегрузки |
| `highest` | — | PARTIAL | `series.highest` · архивные позиционные формы с явным и неявным источником · [Профиль](EXTREMA.md) |
| `highestbars` | — | MISSING | нет совпадающего допуска для перегрузки |
| `hline` | — | MISSING | нет совпадающего допуска для перегрузки |
| `hour` | — | MISSING | нет совпадающего допуска для перегрузки |
| `iff` | — | MISSING | нет совпадающего допуска для перегрузки |
| `input` | — | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `kagi` | — | MISSING | нет совпадающего допуска для перегрузки |
| `linebreak` | — | MISSING | нет совпадающего допуска для перегрузки |
| `linreg` | — | PARTIAL | `indicator_window.linreg` · тип x в источнике описан неполно · постоянная длина 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `log` | — | PARTIAL | `numeric.log` · тип x в источнике описан неполно |
| `log10` | — | PARTIAL | `numeric.log10` · тип x в источнике описан неполно |
| `lowest` | — | PARTIAL | `series.lowest` · архивные позиционные формы с явным и неявным источником · [Профиль](EXTREMA.md) |
| `lowestbars` | — | MISSING | нет совпадающего допуска для перегрузки |
| `macd` | — | PARTIAL | `series.macd` · тип source в источнике описан неполно · тип fastlen в источнике описан неполно · тип slowlen в источнике описан неполно · тип siglen в источнике описан неполно |
| `max` | — | PARTIAL | `numeric.max` · источник описывает 2 аргумента, 3–32 доступны для совместимости OpenPine · [Профиль](NUMERIC_AGGREGATES.md) |
| `min` | — | PARTIAL | `numeric.min` · источник описывает 2 аргумента, 3–32 доступны для совместимости OpenPine · [Профиль](NUMERIC_AGGREGATES.md) |
| `minute` | — | MISSING | нет совпадающего допуска для перегрузки |
| `mom` | — | MISSING | нет совпадающего допуска для перегрузки |
| `month` | — | MISSING | нет совпадающего допуска для перегрузки |
| `na` | — | PARTIAL | `numeric.na` · тип x в источнике описан неполно |
| `nz` | — | PARTIAL | `numeric.nz` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `offset` | — | MISSING | нет совпадающего допуска для перегрузки |
| `percentile_linear_interpolation` | — | PARTIAL | `statistics.percentile_linear_interpolation` · тип x в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `percentile_nearest_rank` | — | PARTIAL | `statistics.percentile_nearest_rank` · тип x в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `percentrank` | — | PARTIAL | `statistics.percentrank` · тип x в источнике описан неполно · тип y в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `pivothigh` | — | PARTIAL | `pivot.pivothigh` · параметры исторического источника восстановлены частично · подтверждение на правом плече, границы по версии · [Профиль](PIVOTS.md) |
| `pivotlow` | — | PARTIAL | `pivot.pivotlow` · параметры исторического источника восстановлены частично · подтверждение на правом плече, границы по версии · [Профиль](PIVOTS.md) |
| `plot` | — | PARTIAL | `colors.plot` · параметры вне профиля dataoffset, editable · тип series в источнике описан неполно · статические параметры оформления · статический offset в пределах ±10 000 · архивные позиционные формы |
| `plotarrow` | — | PARTIAL | `markers.plotarrow` · тип series в источнике описан неполно · статический offset и высота 1–1000 пикселей |
| `plotbar` | — | PARTIAL | `ohlc.plotbar` · базовая сигнатура реконструирована по историческим источникам · первые шесть аргументов позиционные |
| `plotcandle` | — | PARTIAL | `ohlc.plotcandle` · базовая сигнатура реконструирована по историческим источникам · граница позиционных аргументов зависит от версии |
| `plotchar` | — | PARTIAL | `markers.plotchar` · тип series в источнике описан неполно |
| `plotshape` | — | PARTIAL | `markers.plotshape` · тип series в источнике описан неполно |
| `pointfigure` | — | MISSING | нет совпадающего допуска для перегрузки |
| `pow` | — | PARTIAL | `numeric.pow` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `renko` | — | MISSING | нет совпадающего допуска для перегрузки |
| `rising` | — | MISSING | нет совпадающего допуска для перегрузки |
| `rma` | — | PARTIAL | `series.rma` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `roc` | — | MISSING | нет совпадающего допуска для перегрузки |
| `round` | — | PARTIAL | `numeric.round` · тип x в источнике описан неполно |
| `rsi` | — | PARTIAL | `series.rsi` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `sar` | — | PARTIAL | `trend.sar` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `second` | — | MISSING | нет совпадающего допуска для перегрузки |
| `security` | — | PARTIAL | `context.request` · тип z в источнике описан неполно · тип a в источнике описан неполно |
| `sign` | — | PARTIAL | `numeric.sign` · тип x в источнике описан неполно |
| `sin` | — | PARTIAL | `numeric.sin` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `sma` | — | PARTIAL | `series.sma` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `sqrt` | — | PARTIAL | `numeric.sqrt` · тип x в источнике описан неполно |
| `stdev` | — | PARTIAL | `series.stdev.population` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `stoch` | — | PARTIAL | `indicator_window.stoch` · тип x в источнике описан неполно · тип y в источнике описан неполно · тип z в источнике описан неполно · постоянная длина 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `strategy` | — | PARTIAL | `simulation.strategy_magnifier` · параметры исторического источника восстановлены частично |
| `strategy.cancel` | — | PARTIAL | `simulation.cancel` · параметры исторического источника восстановлены частично |
| `strategy.cancel_all` | — | PARTIAL | `simulation.cancel_all` · параметры исторического источника восстановлены частично |
| `strategy.close` | — | PARTIAL | `simulation.close_partial` · параметры исторического источника восстановлены частично |
| `strategy.close_all` | — | PARTIAL | `simulation.close_all` · параметры исторического источника восстановлены частично |
| `strategy.entry` | — | PARTIAL | `simulation.entry_group` · параметры исторического источника восстановлены частично |
| `strategy.exit` | — | PARTIAL | `simulation.exit_extended` · параметры исторического источника восстановлены частично |
| `strategy.order` | — | PARTIAL | `simulation.order` · параметры исторического источника восстановлены частично |
| `strategy.risk.allow_entry_in` | — | PARTIAL | `simulation.risk_allow_entry_in` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_cons_loss_days` | — | PARTIAL | `simulation.risk_max_cons_loss_days` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_drawdown` | — | PARTIAL | `simulation.risk_max_drawdown` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_intraday_filled_orders` | — | PARTIAL | `simulation.risk_max_intraday_filled_orders` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_intraday_loss` | — | PARTIAL | `simulation.risk_max_intraday_loss` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_position_size` | — | PARTIAL | `simulation.risk_max_position_size` · параметры исторического источника восстановлены частично |
| `study` | — | PARTIAL | `harness.indicator` · параметры вне профиля max_bars_back · тип title в источнике описан неполно · тип shorttitle в источнике описан неполно · тип overlay в источнике описан неполно · тип precision в источнике описан неполно · scale.left/right/none, scale.none только с overlay |
| `sum` | — | MISSING | нет совпадающего допуска для перегрузки |
| `swma` | — | PARTIAL | `indicator_window.swma` · тип x в источнике описан неполно · четыре отсчёта, [границы модели](WINDOW_INDICATORS.md) |
| `tan` | — | PARTIAL | `numeric.tan` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `tickerid` | — | MISSING | нет совпадающего допуска для перегрузки |
| `time` | — | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) |
| `tostring` | — | PARTIAL | `scalar.tostring.format` · полный контракт не подтверждён |
| `tr` | — | MISSING | нет совпадающего допуска для перегрузки |
| `tsi` | — | PARTIAL | `momentum.tsi` · тип x в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `valuewhen` | — | PARTIAL | `signals.valuewhen` · тип condition в источнике описан неполно · тип source в источнике описан неполно |
| `variance` | — | PARTIAL | `series.variance.population` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `vwap` | — | PARTIAL | `volume.vwap_daily` · тип x в источнике описан неполно · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `vwma` | — | MISSING | нет совпадающего допуска для перегрузки |
| `weekofyear` | — | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `wma` | — | MISSING | нет совпадающего допуска для перегрузки |
| `year` | — | MISSING | нет совпадающего допуска для перегрузки |

## Pine v2

| Функция | Перегрузка | Статус | Граница |
| --- | ---: | --- | --- |
| `abs` | — | PARTIAL | `numeric.abs` · тип x в источнике описан неполно |
| `acos` | — | PARTIAL | `numeric.acos` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `alertcondition` | — | PARTIAL | `alerts.condition` · параметры исторического источника восстановлены частично · глобальные условия, архивные формы и буквальное сообщение · [Профиль](ALERTS.md) |
| `alma` | — | PARTIAL | `weighted.alma` · тип series в источнике описан неполно · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `asin` | — | PARTIAL | `numeric.asin` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `atan` | — | PARTIAL | `numeric.atan` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `atr` | — | MISSING | нет совпадающего допуска для перегрузки |
| `avg` | — | PARTIAL | `numeric.avg` · 2–6 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `barcolor` | — | PARTIAL | `markers.barcolor` · полный контракт не подтверждён |
| `barssince` | — | PARTIAL | `signals.barssince` · тип condition в источнике описан неполно |
| `bgcolor` | — | PARTIAL | `markers.bgcolor` · полный контракт не подтверждён |
| `cci` | — | PARTIAL | `momentum.cci` · тип x в источнике описан неполно · тип y в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ceil` | — | PARTIAL | `numeric.ceil` · тип x в источнике описан неполно |
| `change` | — | PARTIAL | `series.change` · тип x в источнике описан неполно · тип y в источнике описан неполно · динамическая длина в профиле OpenPine · [История версии](CHANGE_VALUEWHEN.md) |
| `cog` | — | PARTIAL | `weighted.cog` · тип x в источнике описан неполно · тип y в источнике описан неполно · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `color` | — | PARTIAL | `colors.new` · параметры исторического источника восстановлены частично |
| `correlation` | — | PARTIAL | `correlation.pearson` · тип x в источнике описан неполно · тип y в источнике описан неполно · окно определённых пар, квалификатор длины по версии · [Профиль](CORRELATION.md) |
| `cos` | — | PARTIAL | `numeric.cos` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `cross` | — | PARTIAL | `series.cross` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `crossover` | — | PARTIAL | `series.crossover` · параметры исторического источника восстановлены частично |
| `crossunder` | — | PARTIAL | `series.crossunder` · параметры исторического источника восстановлены частично |
| `cum` | — | MISSING | нет совпадающего допуска для перегрузки |
| `dayofmonth` | — | MISSING | нет совпадающего допуска для перегрузки |
| `dayofweek` | — | MISSING | нет совпадающего допуска для перегрузки |
| `dev` | — | PARTIAL | `momentum.dev` · тип x в источнике описан неполно · тип y в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ema` | — | PARTIAL | `series.ema.sma_seed` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `exp` | — | PARTIAL | `numeric.exp` · тип x в источнике описан неполно |
| `falling` | — | MISSING | нет совпадающего допуска для перегрузки |
| `fill` | — | PARTIAL | `bands.fill_hline` / `bands.fill_plot` · тип title в источнике описан неполно · часть типов transp вне профиля · однотипные ссылки, первые три аргумента позиционные |
| `fixnan` | — | MISSING | нет совпадающего допуска для перегрузки |
| `floor` | — | PARTIAL | `numeric.floor` · тип x в источнике описан неполно |
| `heikenashi` | — | MISSING | нет совпадающего допуска для перегрузки |
| `heikinashi` | — | PARTIAL | архивная позиционная форма, исторический security · [Профиль](HEIKIN_ASHI.md) |
| `highest` | — | PARTIAL | `series.highest` · архивные позиционные формы с явным и неявным источником · [Профиль](EXTREMA.md) |
| `highestbars` | — | MISSING | нет совпадающего допуска для перегрузки |
| `hline` | — | MISSING | нет совпадающего допуска для перегрузки |
| `hour` | — | MISSING | нет совпадающего допуска для перегрузки |
| `iff` | — | MISSING | нет совпадающего допуска для перегрузки |
| `input` | — | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `kagi` | — | MISSING | нет совпадающего допуска для перегрузки |
| `linebreak` | — | MISSING | нет совпадающего допуска для перегрузки |
| `linreg` | — | PARTIAL | `indicator_window.linreg` · тип x в источнике описан неполно · постоянная длина 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `log` | — | PARTIAL | `numeric.log` · тип x в источнике описан неполно |
| `log10` | — | PARTIAL | `numeric.log10` · тип x в источнике описан неполно |
| `lowest` | — | PARTIAL | `series.lowest` · архивные позиционные формы с явным и неявным источником · [Профиль](EXTREMA.md) |
| `lowestbars` | — | MISSING | нет совпадающего допуска для перегрузки |
| `macd` | — | PARTIAL | `series.macd` · тип source в источнике описан неполно · тип fastlen в источнике описан неполно · тип slowlen в источнике описан неполно · тип siglen в источнике описан неполно |
| `max` | — | PARTIAL | `numeric.max` · источник описывает 2 аргумента, 3–32 доступны для совместимости OpenPine · [Профиль](NUMERIC_AGGREGATES.md) |
| `mfi` | — | MISSING | нет совпадающего допуска для перегрузки |
| `min` | — | PARTIAL | `numeric.min` · источник описывает 2 аргумента, 3–32 доступны для совместимости OpenPine · [Профиль](NUMERIC_AGGREGATES.md) |
| `minute` | — | MISSING | нет совпадающего допуска для перегрузки |
| `mom` | — | MISSING | нет совпадающего допуска для перегрузки |
| `month` | — | MISSING | нет совпадающего допуска для перегрузки |
| `na` | — | PARTIAL | `numeric.na` · тип x в источнике описан неполно |
| `nz` | — | PARTIAL | `numeric.nz` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `offset` | — | MISSING | нет совпадающего допуска для перегрузки |
| `percentile_linear_interpolation` | — | PARTIAL | `statistics.percentile_linear_interpolation` · тип x в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `percentile_nearest_rank` | — | PARTIAL | `statistics.percentile_nearest_rank` · тип x в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `percentrank` | — | PARTIAL | `statistics.percentrank` · тип x в источнике описан неполно · тип y в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `pivothigh` | — | PARTIAL | `pivot.pivothigh` · параметры исторического источника восстановлены частично · подтверждение на правом плече, границы по версии · [Профиль](PIVOTS.md) |
| `pivotlow` | — | PARTIAL | `pivot.pivotlow` · параметры исторического источника восстановлены частично · подтверждение на правом плече, границы по версии · [Профиль](PIVOTS.md) |
| `plot` | — | PARTIAL | `colors.plot` · параметры вне профиля dataoffset · тип series в источнике описан неполно · статические параметры оформления · статический offset в пределах ±10 000 · архивные позиционные формы |
| `plotarrow` | — | PARTIAL | `markers.plotarrow` · тип series в источнике описан неполно · статический offset и высота 1–1000 пикселей |
| `plotbar` | — | PARTIAL | `ohlc.plotbar` · базовая сигнатура реконструирована по историческим источникам · первые шесть аргументов позиционные |
| `plotcandle` | — | PARTIAL | `ohlc.plotcandle` · базовая сигнатура реконструирована по историческим источникам · граница позиционных аргументов зависит от версии |
| `plotchar` | — | PARTIAL | `markers.plotchar` · тип series в источнике описан неполно |
| `plotshape` | — | PARTIAL | `markers.plotshape` · тип series в источнике описан неполно |
| `pointfigure` | — | MISSING | нет совпадающего допуска для перегрузки |
| `pow` | — | PARTIAL | `numeric.pow` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `renko` | — | MISSING | нет совпадающего допуска для перегрузки |
| `rising` | — | MISSING | нет совпадающего допуска для перегрузки |
| `rma` | — | PARTIAL | `series.rma` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `roc` | — | MISSING | нет совпадающего допуска для перегрузки |
| `round` | — | PARTIAL | `numeric.round` · тип x в источнике описан неполно |
| `rsi` | — | PARTIAL | `series.rsi` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `sar` | — | PARTIAL | `trend.sar` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `second` | — | MISSING | нет совпадающего допуска для перегрузки |
| `security` | — | PARTIAL | `context.request` · параметры исторического источника восстановлены частично |
| `sign` | — | PARTIAL | `numeric.sign` · тип x в источнике описан неполно |
| `sin` | — | PARTIAL | `numeric.sin` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `sma` | — | PARTIAL | `series.sma` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `sqrt` | — | PARTIAL | `numeric.sqrt` · тип x в источнике описан неполно |
| `stdev` | — | PARTIAL | `series.stdev.population` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `stoch` | — | PARTIAL | `indicator_window.stoch` · тип x в источнике описан неполно · тип y в источнике описан неполно · тип z в источнике описан неполно · постоянная длина 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `strategy` | — | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.cancel` | — | PARTIAL | `simulation.cancel` · параметры исторического источника восстановлены частично |
| `strategy.cancel_all` | — | PARTIAL | `simulation.cancel_all` · параметры исторического источника восстановлены частично |
| `strategy.close` | — | PARTIAL | `simulation.close_partial` · параметры исторического источника восстановлены частично |
| `strategy.close_all` | — | PARTIAL | `simulation.close_all` · параметры исторического источника восстановлены частично |
| `strategy.entry` | — | PARTIAL | `simulation.entry_group` · параметры исторического источника восстановлены частично |
| `strategy.exit` | — | PARTIAL | `simulation.exit_extended` · параметры исторического источника восстановлены частично |
| `strategy.order` | — | PARTIAL | `simulation.order` · параметры исторического источника восстановлены частично |
| `strategy.risk.allow_entry_in` | — | PARTIAL | `simulation.risk_allow_entry_in` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_cons_loss_days` | — | PARTIAL | `simulation.risk_max_cons_loss_days` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_drawdown` | — | PARTIAL | `simulation.risk_max_drawdown` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_intraday_filled_orders` | — | PARTIAL | `simulation.risk_max_intraday_filled_orders` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_intraday_loss` | — | PARTIAL | `simulation.risk_max_intraday_loss` · параметры исторического источника восстановлены частично |
| `strategy.risk.max_position_size` | — | PARTIAL | `simulation.risk_max_position_size` · параметры исторического источника восстановлены частично |
| `study` | — | PARTIAL | `harness.indicator` · параметры вне профиля max_bars_back · тип title в источнике описан неполно · тип shorttitle в источнике описан неполно · тип overlay в источнике описан неполно · тип precision в источнике описан неполно · scale.left/right/none, scale.none только с overlay |
| `sum` | — | MISSING | нет совпадающего допуска для перегрузки |
| `swma` | — | PARTIAL | `indicator_window.swma` · тип x в источнике описан неполно · четыре отсчёта, [границы модели](WINDOW_INDICATORS.md) |
| `tan` | — | PARTIAL | `numeric.tan` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `tickerid` | — | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `time` | — | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) |
| `timestamp` | — | PARTIAL | `environment.timestamp6` · параметры исторического источника восстановлены частично |
| `tostring` | — | PARTIAL | `scalar.tostring.format` · полный контракт не подтверждён |
| `tr` | — | MISSING | нет совпадающего допуска для перегрузки |
| `tsi` | — | PARTIAL | `momentum.tsi` · тип x в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `valuewhen` | — | PARTIAL | `signals.valuewhen` · тип condition в источнике описан неполно · тип source в источнике описан неполно |
| `variance` | — | PARTIAL | `series.variance.population` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `vwap` | — | PARTIAL | `volume.vwap_daily` · тип x в источнике описан неполно · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `vwma` | — | MISSING | нет совпадающего допуска для перегрузки |
| `weekofyear` | — | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `wma` | — | MISSING | нет совпадающего допуска для перегрузки |
| `wpr` | — | MISSING | нет совпадающего допуска для перегрузки |
| `year` | — | MISSING | нет совпадающего допуска для перегрузки |

## Pine v3

| Функция | Перегрузка | Статус | Граница |
| --- | ---: | --- | --- |
| `abs` | 0 | PARTIAL | `numeric.abs` · тип x в источнике описан неполно |
| `acos` | 0 | PARTIAL | `numeric.acos` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `alertcondition` | 0 | PARTIAL | `alerts.condition` · тип condition в источнике описан неполно · глобальные условия, архивные формы и буквальное сообщение · [Профиль](ALERTS.md) |
| `alma` | 0 | PARTIAL | `weighted.alma` · тип series в источнике описан неполно · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `asin` | 0 | PARTIAL | `numeric.asin` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `atan` | 0 | PARTIAL | `numeric.atan` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `atr` | 0 | PARTIAL | `series.atr` · полный контракт не подтверждён |
| `avg` | 0 | PARTIAL | `numeric.avg` · 2–6 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `barcolor` | 0 | PARTIAL | `markers.barcolor` · параметры вне профиля editable |
| `barssince` | 0 | PARTIAL | `signals.barssince` · тип condition в источнике описан неполно |
| `bgcolor` | 0 | PARTIAL | `markers.bgcolor` · параметры вне профиля editable |
| `cci` | 0 | PARTIAL | `momentum.cci` · тип source в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ceil` | 0 | PARTIAL | `numeric.ceil` · тип x в источнике описан неполно |
| `change` | 0 | PARTIAL | `series.change` · тип source в источнике описан неполно · динамическая длина в профиле OpenPine · [История версии](CHANGE_VALUEWHEN.md) |
| `cog` | 0 | PARTIAL | `weighted.cog` · тип source в источнике описан неполно · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `color` | 0 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `correlation` | 0 | PARTIAL | `correlation.pearson` · тип source_a в источнике описан неполно · тип source_b в источнике описан неполно · окно определённых пар, квалификатор длины по версии · [Профиль](CORRELATION.md) |
| `cos` | 0 | PARTIAL | `numeric.cos` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `cross` | 0 | PARTIAL | `series.cross` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `crossover` | 0 | PARTIAL | `series.crossover` · полный контракт не подтверждён |
| `crossunder` | 0 | PARTIAL | `series.crossunder` · полный контракт не подтверждён |
| `cum` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `dayofmonth` | 0 | PARTIAL | `environment.dayofmonth` · тип time в источнике описан неполно |
| `dayofweek` | 0 | PARTIAL | `environment.dayofweek` · тип time в источнике описан неполно |
| `dev` | 0 | PARTIAL | `momentum.dev` · тип source в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ema` | 0 | PARTIAL | `series.ema.sma_seed` · тип source в источнике описан неполно |
| `exp` | 0 | PARTIAL | `numeric.exp` · тип x в источнике описан неполно |
| `falling` | 0 | PARTIAL | `signals.falling` · тип source в источнике описан неполно |
| `fill` | 0 | PARTIAL | `bands.fill_hline` / `bands.fill_plot` · параметры вне профиля editable · однотипные ссылки, первые три аргумента позиционные |
| `fixnan` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `floor` | 0 | PARTIAL | `numeric.floor` · тип x в источнике описан неполно |
| `heikinashi` | 0 | PARTIAL | фиксированный тикер, исторический security · [Профиль](HEIKIN_ASHI.md) |
| `highest` | 0 | PARTIAL | `series.highest` · тип source в источнике описан неполно |
| `highestbars` | 0 | PARTIAL | `signals.highestbars` · тип source в источнике описан неполно |
| `hline` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `hour` | 0 | PARTIAL | `environment.hour` · тип time в источнике описан неполно |
| `iff` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `input` | 0 | PARTIAL | [глобальное объявление, в том числе type=session](TRADING_SESSIONS.md) |
| `kagi` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `linebreak` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `linreg` | 0 | PARTIAL | `indicator_window.linreg` · тип source в источнике описан неполно · постоянная длина 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `log` | 0 | PARTIAL | `numeric.log` · тип x в источнике описан неполно |
| `log10` | 0 | PARTIAL | `numeric.log10` · тип x в источнике описан неполно |
| `lowest` | 0 | PARTIAL | `series.lowest` · тип source в источнике описан неполно |
| `lowestbars` | 0 | PARTIAL | `signals.lowestbars` · тип source в источнике описан неполно |
| `macd` | 0 | PARTIAL | `series.macd` · тип source в источнике описан неполно |
| `max` | 0 | PARTIAL | `numeric.max` · источник описывает 2 аргумента, 3–32 доступны для совместимости OpenPine · [Профиль](NUMERIC_AGGREGATES.md) |
| `min` | 0 | PARTIAL | `numeric.min` · источник описывает 2 аргумента, 3–32 доступны для совместимости OpenPine · [Профиль](NUMERIC_AGGREGATES.md) |
| `minute` | 0 | PARTIAL | `environment.minute` · тип time в источнике описан неполно |
| `mom` | 0 | PARTIAL | `signals.mom` · тип source в источнике описан неполно |
| `month` | 0 | PARTIAL | `environment.month` · тип time в источнике описан неполно |
| `na` | 0 | PARTIAL | `numeric.na` · тип x в источнике описан неполно |
| `nz` | 0 | PARTIAL | `numeric.nz` · тип x в источнике описан неполно |
| `offset` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `percentile_linear_interpolation` | 0 | PARTIAL | `statistics.percentile_linear_interpolation` · тип source в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `percentile_nearest_rank` | 0 | PARTIAL | `statistics.percentile_nearest_rank` · тип source в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `percentrank` | 0 | PARTIAL | `statistics.percentrank` · тип source в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `pivothigh` | 0 | PARTIAL | `pivot.pivothigh` · тип source в источнике описан неполно · тип leftbars в источнике описан неполно · тип rightbars в источнике описан неполно · подтверждение на правом плече, границы по версии · [Профиль](PIVOTS.md) |
| `pivotlow` | 0 | PARTIAL | `pivot.pivotlow` · тип source в источнике описан неполно · тип leftbars в источнике описан неполно · тип rightbars в источнике описан неполно · подтверждение на правом плече, границы по версии · [Профиль](PIVOTS.md) |
| `plot` | 0 | PARTIAL | `colors.plot` · параметры вне профиля editable · тип series в источнике описан неполно · статические параметры оформления · статический offset в пределах ±10 000 |
| `plotarrow` | 0 | PARTIAL | `markers.plotarrow` · параметры вне профиля editable · тип series в источнике описан неполно · статический offset и высота 1–1000 пикселей |
| `plotbar` | 0 | PARTIAL | `ohlc.plotbar` · параметры вне профиля editable · тип open в источнике описан неполно · тип high в источнике описан неполно · тип low в источнике описан неполно · тип close в источнике описан неполно · первые шесть аргументов позиционные |
| `plotcandle` | 0 | PARTIAL | `ohlc.plotcandle` · параметры вне профиля editable · тип open в источнике описан неполно · тип high в источнике описан неполно · тип low в источнике описан неполно · тип close в источнике описан неполно · граница позиционных аргументов зависит от версии |
| `plotchar` | 0 | PARTIAL | `markers.plotchar` · параметры вне профиля editable · тип series в источнике описан неполно |
| `plotshape` | 0 | PARTIAL | `markers.plotshape` · параметры вне профиля editable · тип series в источнике описан неполно |
| `pointfigure` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `pow` | 0 | PARTIAL | `numeric.pow` · тип base в источнике описан неполно |
| `renko` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `rising` | 0 | PARTIAL | `signals.rising` · тип source в источнике описан неполно |
| `rma` | 0 | PARTIAL | `series.rma` · тип source в источнике описан неполно |
| `roc` | 0 | PARTIAL | `signals.roc` · тип source в источнике описан неполно |
| `round` | 0 | PARTIAL | `numeric.round` · тип x в источнике описан неполно |
| `rsi` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `sar` | 0 | PARTIAL | `trend.sar` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `second` | 0 | PARTIAL | `environment.second` · тип time в источнике описан неполно |
| `security` | 0 | PARTIAL | `context.request` · тип expression в источнике описан неполно |
| `sign` | 0 | PARTIAL | `numeric.sign` · тип x в источнике описан неполно |
| `sin` | 0 | PARTIAL | `numeric.sin` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `sma` | 0 | PARTIAL | `series.sma` · тип source в источнике описан неполно |
| `sqrt` | 0 | PARTIAL | `numeric.sqrt` · тип x в источнике описан неполно |
| `stdev` | 0 | PARTIAL | `series.stdev.population` · тип source в источнике описан неполно |
| `stoch` | 0 | PARTIAL | `indicator_window.stoch` · тип source в источнике описан неполно · тип high в источнике описан неполно · тип low в источнике описан неполно · постоянная длина 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `strategy` | 0 | PARTIAL | `simulation.strategy` · параметры вне профиля shorttitle, calc_on_order_fills, calc_on_every_tick, max_bars_back, backtest_fill_limits_assumption, currency, linktoseries · тип initial_capital в источнике описан неполно · scale.left/right/none, scale.none только с overlay |
| `strategy.cancel` | 0 | PARTIAL | `simulation.cancel` · полный контракт не подтверждён |
| `strategy.cancel_all` | 0 | PARTIAL | `simulation.cancel_all` · полный контракт не подтверждён |
| `strategy.close` | 0 | PARTIAL | `simulation.close` · полный контракт не подтверждён |
| `strategy.close_all` | 0 | PARTIAL | `simulation.close_all` · полный контракт не подтверждён |
| `strategy.entry` | 0 | PARTIAL | `simulation.entry_group` · параметры вне профиля comment |
| `strategy.exit` | 0 | PARTIAL | `simulation.exit_extended` · параметры вне профиля comment |
| `strategy.order` | 0 | PARTIAL | `simulation.order` · параметры вне профиля comment |
| `strategy.risk.allow_entry_in` | 0 | PARTIAL | `simulation.risk_allow_entry_in` · полный контракт не подтверждён |
| `strategy.risk.max_cons_loss_days` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.risk.max_drawdown` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.risk.max_intraday_filled_orders` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.risk.max_intraday_loss` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.risk.max_position_size` | 0 | PARTIAL | `simulation.risk_max_position_size` · полный контракт не подтверждён |
| `study` | 0 | PARTIAL | `harness.indicator` · параметры вне профиля max_bars_back, linktoseries · scale.left/right/none, scale.none только с overlay |
| `sum` | 0 | PARTIAL | `signals.sum` · тип source в источнике описан неполно |
| `swma` | 0 | PARTIAL | `indicator_window.swma` · тип x в источнике описан неполно · четыре отсчёта, [границы модели](WINDOW_INDICATORS.md) |
| `tan` | 0 | PARTIAL | `numeric.tan` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `tickerid` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `time` | 0 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) |
| `timestamp` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `tostring` | 0 | PARTIAL | `scalar.tostring.format` · полный контракт не подтверждён |
| `tr` | 0 | PARTIAL | `series.tr` · полный контракт не подтверждён |
| `tsi` | 0 | PARTIAL | `momentum.tsi` · тип source в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `valuewhen` | 0 | PARTIAL | `signals.valuewhen` · тип condition в источнике описан неполно · тип source в источнике описан неполно · тип occurrence в источнике описан неполно |
| `variance` | 0 | PARTIAL | `series.variance.population` · тип source в источнике описан неполно |
| `vwap` | 0 | PARTIAL | `volume.vwap_daily` · тип x в источнике описан неполно · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `vwma` | 0 | PARTIAL | `signals.vwma` · тип source в источнике описан неполно |
| `weekofyear` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `wma` | 0 | PARTIAL | `signals.wma` · тип source в источнике описан неполно |
| `year` | 0 | PARTIAL | `environment.year` · тип time в источнике описан неполно |

## Pine v4

| Функция | Перегрузка | Статус | Граница |
| --- | ---: | --- | --- |
| `abs` | 0 | PARTIAL | `numeric.abs` · тип x в источнике описан неполно |
| `acos` | 0 | PARTIAL | `numeric.acos` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `alert` | 0 | PARTIAL | `alerts.call` · локальные события replay, три режима частоты и ограниченное сообщение · [Профиль](ALERTS.md) |
| `alertcondition` | 0 | PARTIAL | `alerts.condition` · тип condition в источнике описан неполно · глобальные условия, журнал replay, подстановки в отдельном профиле · [Профиль](ALERTS.md) |
| `alma` | 0 | PARTIAL | `weighted.alma` · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `array.avg` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.clear` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.concat` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.copy` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.covariance` | 0 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.fill` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.get` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.includes` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.indexof` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.insert` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.join` | 0 | PARTIAL | [массивы строк, объединение чисел, ограничения размера и работы](ARRAY_TEXT.md) |
| `array.lastindexof` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.max` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.median` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.min` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.mode` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.new_bool` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_box` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.new_color` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_float` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_int` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_label` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.new_line` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.new_string` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_table` | 0 | PARTIAL | [изменяемый массив ссылок, независимые ячейки и восстановление](DRAWING_REFERENCE_ARRAYS.md) |
| `array.pop` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.push` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.range` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.remove` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.reverse` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.set` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.shift` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.size` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.slice` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.sort` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.standardize` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.stdev` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.sum` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.unshift` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.variance` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `asin` | 0 | PARTIAL | `numeric.asin` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `atan` | 0 | PARTIAL | `numeric.atan` · тип x в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `atr` | 0 | PARTIAL | `series.atr` · полный контракт не подтверждён |
| `avg` | 0 | PARTIAL | `numeric.avg` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `barcolor` | 0 | PARTIAL | `markers.barcolor` · параметры вне профиля editable |
| `barssince` | 0 | PARTIAL | `signals.barssince` · тип condition в источнике описан неполно |
| `bb` | 0 | PARTIAL | `channel.bb` · длина series int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `bbw` | 0 | PARTIAL | `channel.bbw` · длина series int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `bgcolor` | 0 | PARTIAL | `markers.bgcolor` · параметры вне профиля editable |
| `bool` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `box` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `box.delete` | 0 | PARTIAL | `box.delete` · полный контракт не подтверждён |
| `box.get_bottom` | 0 | PARTIAL | `box.get_bottom` · полный контракт не подтверждён |
| `box.get_left` | 0 | PARTIAL | `box.get_left` · полный контракт не подтверждён |
| `box.get_right` | 0 | PARTIAL | `box.get_right` · полный контракт не подтверждён |
| `box.get_top` | 0 | PARTIAL | `box.get_top` · полный контракт не подтверждён |
| `box.new` | 0 | PARTIAL | `box.new_styled` · xloc ограничен const · индикаторы и стратегии, координаты по допущенным профилям xloc |
| `box.set_bgcolor` | 0 | PARTIAL | `box.set_bgcolor` · полный контракт не подтверждён |
| `box.set_border_color` | 0 | PARTIAL | `box.set_border_color` · полный контракт не подтверждён |
| `box.set_border_style` | 0 | PARTIAL | `box.set_border_style` · полный контракт не подтверждён |
| `box.set_border_width` | 0 | PARTIAL | `box.set_border_width` · полный контракт не подтверждён |
| `box.set_bottom` | 0 | PARTIAL | `box.set_bottom` · полный контракт не подтверждён |
| `box.set_extend` | 0 | PARTIAL | `box.set_extend` · полный контракт не подтверждён |
| `box.set_left` | 0 | PARTIAL | `box.set_left` · полный контракт не подтверждён |
| `box.set_lefttop` | 0 | PARTIAL | `box.set_lefttop` · полный контракт не подтверждён |
| `box.set_right` | 0 | PARTIAL | `box.set_right` · полный контракт не подтверждён |
| `box.set_rightbottom` | 0 | PARTIAL | `box.set_rightbottom` · полный контракт не подтверждён |
| `box.set_top` | 0 | PARTIAL | `box.set_top` · полный контракт не подтверждён |
| `cci` | 0 | PARTIAL | `momentum.cci` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ceil` | 0 | PARTIAL | `numeric.ceil` · тип x в источнике описан неполно |
| `change` | 0 | PARTIAL | `series.change` · полный контракт не подтверждён |
| `cmo` | 0 | PARTIAL | `momentum.cmo` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `cog` | 0 | PARTIAL | `weighted.cog` · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `color` | 0 | PARTIAL | `colors.cast` · тип x в источнике описан неполно |
| `color.b` | 0 | PARTIAL | `colors.b` · полный контракт не подтверждён |
| `color.from_gradient` | 0 | PARTIAL | `colors.gradient` · полный контракт не подтверждён |
| `color.g` | 0 | PARTIAL | `colors.g` · полный контракт не подтверждён |
| `color.new` | 0 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `color.r` | 0 | PARTIAL | `colors.r` · полный контракт не подтверждён |
| `color.rgb` | 0 | PARTIAL | `colors.rgb` · полный контракт не подтверждён |
| `color.t` | 0 | PARTIAL | `colors.t` · полный контракт не подтверждён |
| `correlation` | 0 | PARTIAL | `correlation.pearson` · окно определённых пар, квалификатор длины по версии · [Профиль](CORRELATION.md) |
| `cos` | 0 | PARTIAL | `numeric.cos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `cross` | 0 | PARTIAL | `series.cross` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `crossover` | 0 | PARTIAL | `series.crossover` · полный контракт не подтверждён |
| `crossunder` | 0 | PARTIAL | `series.crossunder` · полный контракт не подтверждён |
| `cum` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `dayofmonth` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `dayofweek` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `dev` | 0 | PARTIAL | `momentum.dev` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `dividends` | 0 | PARTIAL | фиксированный рынок · [корпоративные события](CORPORATE_ACTIONS.md) |
| `dmi` | 0 | PARTIAL | `trend.dmi` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `earnings` | 0 | PARTIAL | фиксированный рынок и исходная валюта · [EPS](EARNINGS.md) |
| `ema` | 0 | PARTIAL | `series.ema.sma_seed` · тип source в источнике описан неполно |
| `exp` | 0 | PARTIAL | `numeric.exp` · тип x в источнике описан неполно |
| `falling` | 0 | PARTIAL | `swindow.falling` · полный контракт не подтверждён |
| `fill` | 0 | PARTIAL | `bands.fill_hline` / `bands.fill_plot` · параметры вне профиля editable · однотипные ссылки, первые три аргумента позиционные |
| `financial` | 0 | PARTIAL | четыре показателя отчётности · [периоды и публикации](FINANCIAL.md) |
| `fixnan` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `float` | 0 | PARTIAL | `numeric.float` · тип x в источнике описан неполно |
| `floor` | 0 | PARTIAL | `numeric.floor` · тип x в источнике описан неполно |
| `heikinashi` | 0 | PARTIAL | фиксированный тикер, исторический security · [Профиль](HEIKIN_ASHI.md) |
| `highest` | 0 | PARTIAL | `window.highest` · полный контракт не подтверждён |
| `highestbars` | 0 | PARTIAL | `swindow.highestbars` · полный контракт не подтверждён |
| `hline` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `hma` | 0 | PARTIAL | `weighted.hma` · тип source в источнике описан неполно · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `hour` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `iff` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `input` | 0 | PARTIAL | [глобальное объявление · [type=input.session](TRADING_SESSIONS.md) · type=input.time](DATE_INPUTS.md) |
| `int` | 0 | PARTIAL | `numeric.int` · тип x в источнике описан неполно |
| `kagi` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `kc` | 0 | PARTIAL | `channel.kc` · тип series в источнике описан неполно · длина simple int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `kcw` | 0 | PARTIAL | `channel.kcw` · тип series в источнике описан неполно · длина simple int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `label` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `label.delete` | 0 | PARTIAL | `label.delete` · полный контракт не подтверждён |
| `label.get_text` | 0 | PARTIAL | `label.get_text` · полный контракт не подтверждён |
| `label.get_x` | 0 | PARTIAL | `label.get_x` · полный контракт не подтверждён |
| `label.get_y` | 0 | PARTIAL | `label.get_y` · полный контракт не подтверждён |
| `label.new` | 0 | PARTIAL | `label.new_text` · тип x в источнике описан неполно · тип y в источнике описан неполно · индикаторы и стратегии, координаты по допущенным профилям xloc и yloc |
| `label.set_color` | 0 | PARTIAL | `label.set_color` · полный контракт не подтверждён |
| `label.set_size` | 0 | PARTIAL | `label.set_size` · полный контракт не подтверждён |
| `label.set_style` | 0 | PARTIAL | `label.set_style` · полный контракт не подтверждён |
| `label.set_text` | 0 | PARTIAL | `label.set_text` · полный контракт не подтверждён |
| `label.set_textalign` | 0 | PARTIAL | `label.set_textalign` · полный контракт не подтверждён |
| `label.set_textcolor` | 0 | PARTIAL | `label.set_textcolor` · полный контракт не подтверждён |
| `label.set_tooltip` | 0 | PARTIAL | `label.set_tooltip` · полный контракт не подтверждён |
| `label.set_x` | 0 | PARTIAL | `label.set_x` · тип x в источнике описан неполно |
| `label.set_xloc` | 0 | PARTIAL | `label.set_xloc` · тип x в источнике описан неполно |
| `label.set_xy` | 0 | PARTIAL | `label.set_xy` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `label.set_y` | 0 | PARTIAL | `label.set_y` · тип y в источнике описан неполно |
| `label.set_yloc` | 0 | PARTIAL | `label.set_yloc` · полный контракт не подтверждён |
| `line` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `line.delete` | 0 | PARTIAL | `line.delete` · полный контракт не подтверждён |
| `line.get_price` | 0 | PARTIAL | `line.get_price` · тип x в источнике описан неполно |
| `line.get_x1` | 0 | PARTIAL | `line.get_x1` · полный контракт не подтверждён |
| `line.get_x2` | 0 | PARTIAL | `line.get_x2` · полный контракт не подтверждён |
| `line.get_y1` | 0 | PARTIAL | `line.get_y1` · полный контракт не подтверждён |
| `line.get_y2` | 0 | PARTIAL | `line.get_y2` · полный контракт не подтверждён |
| `line.new` | 0 | PARTIAL | `line.new_styled` · тип x1 в источнике описан неполно · тип y1 в источнике описан неполно · тип x2 в источнике описан неполно · тип y2 в источнике описан неполно · индикаторы и стратегии, закрытые xloc и стили, ширина 1–64 |
| `line.set_color` | 0 | PARTIAL | `line.set_color` · полный контракт не подтверждён |
| `line.set_extend` | 0 | PARTIAL | `line.set_extend` · полный контракт не подтверждён |
| `line.set_style` | 0 | PARTIAL | `line.set_style` · полный контракт не подтверждён |
| `line.set_width` | 0 | PARTIAL | `line.set_width` · полный контракт не подтверждён |
| `line.set_x1` | 0 | PARTIAL | `line.set_x1` · тип x в источнике описан неполно |
| `line.set_x2` | 0 | PARTIAL | `line.set_x2` · тип x в источнике описан неполно |
| `line.set_xloc` | 0 | PARTIAL | `line.set_xloc` · тип x1 в источнике описан неполно · тип x2 в источнике описан неполно |
| `line.set_xy1` | 0 | PARTIAL | `line.set_xy1` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `line.set_xy2` | 0 | PARTIAL | `line.set_xy2` · тип x в источнике описан неполно · тип y в источнике описан неполно |
| `line.set_y1` | 0 | PARTIAL | `line.set_y1` · тип y в источнике описан неполно |
| `line.set_y2` | 0 | PARTIAL | `line.set_y2` · тип y в источнике описан неполно |
| `linebreak` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `linreg` | 0 | PARTIAL | `indicator_window.linreg` · длина series int 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `log` | 0 | PARTIAL | `numeric.log` · тип x в источнике описан неполно |
| `log10` | 0 | PARTIAL | `numeric.log10` · тип x в источнике описан неполно |
| `lowest` | 0 | PARTIAL | `window.lowest` · полный контракт не подтверждён |
| `lowestbars` | 0 | PARTIAL | `swindow.lowestbars` · полный контракт не подтверждён |
| `macd` | 0 | PARTIAL | `series.macd` · тип source в источнике описан неполно |
| `max` | 0 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `max_bars_back` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `median` | 0 | PARTIAL | `statistics.median` · тип source в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `mfi` | 0 | PARTIAL | `volume.mfi` · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `min` | 0 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `minute` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `mode` | 0 | PARTIAL | `statistics.mode` · тип source в источнике описан неполно · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `mom` | 0 | PARTIAL | `swindow.mom` · полный контракт не подтверждён |
| `month` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `na` | 0 | PARTIAL | `numeric.na` · тип x в источнике описан неполно |
| `nz` | 0 | PARTIAL | `numeric.nz` · тип x в источнике описан неполно |
| `offset` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `percentile_linear_interpolation` | 0 | PARTIAL | `statistics.percentile_linear_interpolation` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `percentile_nearest_rank` | 0 | PARTIAL | `statistics.percentile_nearest_rank` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `percentrank` | 0 | PARTIAL | `statistics.percentrank` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `pivothigh` | 0 | PARTIAL | `pivot.pivothigh` · тип source в источнике описан неполно · тип leftbars в источнике описан неполно · тип rightbars в источнике описан неполно · подтверждение на правом плече, границы по версии · [Профиль](PIVOTS.md) |
| `pivotlow` | 0 | PARTIAL | `pivot.pivotlow` · тип source в источнике описан неполно · тип leftbars в источнике описан неполно · тип rightbars в источнике описан неполно · подтверждение на правом плече, границы по версии · [Профиль](PIVOTS.md) |
| `plot` | 0 | PARTIAL | `colors.plot` · параметры вне профиля editable · тип series в источнике описан неполно · display ограничен display.all и display.none · статические параметры оформления · статический offset в пределах ±10 000 |
| `plotarrow` | 0 | PARTIAL | `markers.plotarrow` · параметры вне профиля editable · тип series в источнике описан неполно · display ограничен display.all и display.none · статический offset и высота 1–1000 пикселей |
| `plotbar` | 0 | PARTIAL | `ohlc.plotbar` · параметры вне профиля editable · тип open в источнике описан неполно · тип high в источнике описан неполно · тип low в источнике описан неполно · тип close в источнике описан неполно · display ограничен display.all и display.none · первые шесть аргументов позиционные |
| `plotcandle` | 0 | PARTIAL | `ohlc.plotcandle` · параметры вне профиля editable · тип open в источнике описан неполно · тип high в источнике описан неполно · тип low в источнике описан неполно · тип close в источнике описан неполно · display ограничен display.all и display.none · граница позиционных аргументов зависит от версии |
| `plotchar` | 0 | PARTIAL | `markers.plotchar` · параметры вне профиля editable · тип series в источнике описан неполно · display ограничен display.all и display.none |
| `plotshape` | 0 | PARTIAL | `markers.plotshape` · параметры вне профиля editable · тип series в источнике описан неполно · display ограничен display.all и display.none |
| `pointfigure` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `pow` | 0 | PARTIAL | `numeric.pow` · полный контракт не подтверждён |
| `quandl` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `random` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `range` | 0 | PARTIAL | `extrema.range` · тип source в источнике описан неполно · [окна и накопительные экстремумы](EXTREMA.md) |
| `renko` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `rising` | 0 | PARTIAL | `swindow.rising` · полный контракт не подтверждён |
| `rma` | 0 | PARTIAL | `series.rma` · тип source в источнике описан неполно |
| `roc` | 0 | PARTIAL | `swindow.roc` · полный контракт не подтверждён |
| `round` | 0 | PARTIAL | `numeric.round` · параметры вне профиля x · тип number в источнике описан неполно |
| `round_to_mintick` | 0 | PARTIAL | `environment.round_to_mintick` · [шаг из снимка инструмента](TICK_PRICE.md) |
| `rsi` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `sar` | 0 | PARTIAL | `trend.sar` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `second` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `security` | 0 | PARTIAL | `context.request` · тип expression в источнике описан неполно |
| `sign` | 0 | PARTIAL | `numeric.sign` · тип x в источнике описан неполно |
| `sin` | 0 | PARTIAL | `numeric.sin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `sma` | 0 | PARTIAL | `series.sma` · полный контракт не подтверждён |
| `splits` | 0 | PARTIAL | фиксированный рынок · [корпоративные события](CORPORATE_ACTIONS.md) |
| `sqrt` | 0 | PARTIAL | `numeric.sqrt` · тип x в источнике описан неполно |
| `stdev` | 0 | PARTIAL | `window.stdev.population` · полный контракт не подтверждён |
| `stoch` | 0 | PARTIAL | `indicator_window.stoch` · длина series int 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `str.format` | 0 | PARTIAL | `strings.format` · скаляры и числовые форматы · [Профиль](STRING_FORMAT.md) |
| `str.length` | 0 | PARTIAL | `scalar.length` · полный контракт не подтверждён |
| `str.replace_all` | 0 | PARTIAL | `scalar.replace_all` · полный контракт не подтверждён |
| `str.split` | 0 | PARTIAL | [массивы строк, объединение чисел, ограничения размера и работы](ARRAY_TEXT.md) |
| `strategy` | 0 | PARTIAL | `simulation.strategy_cycle` · параметры вне профиля shorttitle, max_bars_back, backtest_fill_limits_assumption, currency, margin_long, margin_short · scale.left/right/none, scale.none только с overlay |
| `strategy.cancel` | 0 | PARTIAL | `simulation.cancel` · id ограничен const |
| `strategy.cancel_all` | 0 | PARTIAL | `simulation.cancel_all` · полный контракт не подтверждён |
| `strategy.close` | 0 | PARTIAL | `simulation.close_partial` · параметры вне профиля comment · id ограничен const |
| `strategy.close_all` | 0 | PARTIAL | `simulation.close_all` · параметры вне профиля comment |
| `strategy.entry` | 0 | PARTIAL | `simulation.entry_group` · параметры вне профиля comment · id ограничен const |
| `strategy.exit` | 0 | PARTIAL | `simulation.exit_extended` · параметры вне профиля comment · id ограничен const · from_entry ограничен const |
| `strategy.order` | 0 | PARTIAL | `simulation.order` · параметры вне профиля comment · id ограничен const |
| `strategy.risk.allow_entry_in` | 0 | PARTIAL | `simulation.risk_allow_entry_in` · полный контракт не подтверждён |
| `strategy.risk.max_cons_loss_days` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.risk.max_drawdown` | 0 | PARTIAL | `simulation.risk_max_drawdown` · полный контракт не подтверждён |
| `strategy.risk.max_intraday_filled_orders` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.risk.max_intraday_loss` | 0 | PARTIAL | `simulation.risk_max_intraday_loss` · полный контракт не подтверждён |
| `strategy.risk.max_position_size` | 0 | PARTIAL | `simulation.risk_max_position_size` · полный контракт не подтверждён |
| `string` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `study` | 0 | PARTIAL | `harness.indicator` · параметры вне профиля max_bars_back, resolution, resolution_gaps, linktoseries · scale.left/right/none, scale.none только с overlay |
| `sum` | 0 | PARTIAL | `window.sum` · полный контракт не подтверждён |
| `supertrend` | 0 | PARTIAL | `trend.supertrend` · тип factor в источнике описан неполно · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `swma` | 0 | PARTIAL | `indicator_window.swma` · тип x в источнике описан неполно · четыре отсчёта, [границы модели](WINDOW_INDICATORS.md) |
| `table` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `table.cell` | 0 | PARTIAL | `table.cell_layout` · размеры 0–100% и типографика с границами по версии |
| `table.cell_set_bgcolor` | 0 | PARTIAL | `table.cell_set_bgcolor` · полный контракт не подтверждён |
| `table.cell_set_height` | 0 | PARTIAL | `table.cell_set_height` · полный контракт не подтверждён |
| `table.cell_set_text` | 0 | PARTIAL | `table.cell_set_text` · полный контракт не подтверждён |
| `table.cell_set_text_color` | 0 | PARTIAL | `table.cell_set_text_color` · полный контракт не подтверждён |
| `table.cell_set_text_halign` | 0 | PARTIAL | `table.cell_set_text_halign` · полный контракт не подтверждён |
| `table.cell_set_text_size` | 0 | PARTIAL | `table.cell_set_text_size` · полный контракт не подтверждён |
| `table.cell_set_text_valign` | 0 | PARTIAL | `table.cell_set_text_valign` · полный контракт не подтверждён |
| `table.cell_set_width` | 0 | PARTIAL | `table.cell_set_width` · полный контракт не подтверждён |
| `table.clear` | 0 | PARTIAL | `table.clear` · полный контракт не подтверждён |
| `table.delete` | 0 | PARTIAL | `table.delete` · полный контракт не подтверждён |
| `table.new` | 0 | PARTIAL | `table.new_styled` · индикаторы и стратегии, девять позиций, 256 ячеек, именованные frame_color, frame_width, border_color, border_width |
| `table.set_bgcolor` | 0 | PARTIAL | `table.set_bgcolor` · полный контракт не подтверждён |
| `table.set_border_color` | 0 | PARTIAL | `table.set_border_color` · полный контракт не подтверждён |
| `table.set_border_width` | 0 | PARTIAL | `table.set_border_width` · полный контракт не подтверждён |
| `table.set_frame_color` | 0 | PARTIAL | `table.set_frame_color` · полный контракт не подтверждён |
| `table.set_frame_width` | 0 | PARTIAL | `table.set_frame_width` · полный контракт не подтверждён |
| `table.set_position` | 0 | PARTIAL | `table.set_position` · полный контракт не подтверждён |
| `tan` | 0 | PARTIAL | `numeric.tan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `tickerid` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `time` | 0 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) |
| `time_close` | 0 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) |
| `timestamp` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) · [Константная дата](DATE_INPUTS.md) |
| `todegrees` | 0 | PARTIAL | `numeric.todegrees` · тип radians в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `tonumber` | 0 | PARTIAL | `scalar.tonumber` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `toradians` | 0 | PARTIAL | `numeric.toradians` · тип degrees в источнике описан неполно · [числовой профиль углов](TRIGONOMETRY.md) |
| `tostring` | 0 | PARTIAL | `scalar.tostring.format` · тип x в источнике описан неполно · [форматирование по шагу инструмента](TICK_PRICE.md) |
| `tr` | 0 | PARTIAL | `series.tr` · полный контракт не подтверждён |
| `tsi` | 0 | PARTIAL | `momentum.tsi` · тип source в источнике описан неполно · [версии, длины и правила прогрева](MOMENTUM.md) |
| `valuewhen` | 0 | PARTIAL | `signals.valuewhen` · тип condition в источнике описан неполно · тип source в источнике описан неполно · тип occurrence в источнике описан неполно |
| `variance` | 0 | PARTIAL | `window.variance.population` · полный контракт не подтверждён |
| `vwap` | 0 | PARTIAL | `volume.vwap_daily` · тип x в источнике описан неполно · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `vwma` | 0 | PARTIAL | `swindow.vwma` · полный контракт не подтверждён |
| `weekofyear` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `wma` | 0 | PARTIAL | `window.wma` · полный контракт не подтверждён |
| `wpr` | 0 | PARTIAL | `momentum.wpr` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `year` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |

## Pine v5

| Функция | Перегрузка | Статус | Граница |
| --- | ---: | --- | --- |
| `alert` | 0 | PARTIAL | `alerts.call` · локальные события replay, три режима частоты и ограниченное сообщение · [Профиль](ALERTS.md) |
| `alertcondition` | 0 | PARTIAL | `alerts.condition` · глобальные условия, журнал replay, подстановки в отдельном профиле · [Профиль](ALERTS.md) |
| `array.abs` | 0 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.abs` | 1 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.avg` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.avg` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.binary_search` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.binary_search_leftmost` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.binary_search_rightmost` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.clear` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.concat` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.copy` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.covariance` | 0 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.every` | 0 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.fill` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.first` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.from` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 10 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 11 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 2 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 3 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 4 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 5 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 6 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 7 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 8 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 9 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.get` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.includes` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.indexof` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.insert` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.join` | 0 | PARTIAL | [массивы строк, объединение чисел, ограничения размера и работы](ARRAY_TEXT.md) |
| `array.last` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.lastindexof` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.max` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.max` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.max` | 2 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.max` | 3 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.median` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.median` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.min` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.min` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.min` | 2 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.min` | 3 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.mode` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.mode` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.new<type>` | 0 | PARTIAL | [закрытый набор типов, в том числе графические ID](ARRAYS.md) |
| `array.new_bool` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_box` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.new_color` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_float` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_int` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_label` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.new_line` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.new_linefill` | 0 | PARTIAL | [изменяемый массив ссылок, независимые ячейки и восстановление](DRAWING_REFERENCE_ARRAYS.md) |
| `array.new_string` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_table` | 0 | PARTIAL | [изменяемый массив ссылок, независимые ячейки и восстановление](DRAWING_REFERENCE_ARRAYS.md) |
| `array.percentile_linear_interpolation` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentile_linear_interpolation` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentile_nearest_rank` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentile_nearest_rank` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentrank` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentrank` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.pop` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.push` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.range` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.range` | 1 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.remove` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.reverse` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.set` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.shift` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.size` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.slice` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.some` | 0 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.sort` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.sort_indices` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.standardize` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.standardize` | 1 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.stdev` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.stdev` | 1 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.sum` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.sum` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.unshift` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.variance` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.variance` | 1 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `barcolor` | 0 | PARTIAL | `markers.barcolor` · параметры вне профиля editable · offset ограничен input · display ограничен display.all и display.none |
| `bgcolor` | 0 | PARTIAL | `markers.bgcolor` · параметры вне профиля editable · offset ограничен input · тип transp в источнике описан неполно · display ограничен display.all и display.none |
| `bool` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `bool` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `bool` | 2 | MISSING | нет совпадающего допуска для перегрузки |
| `bool` | 3 | MISSING | нет совпадающего допуска для перегрузки |
| `box` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `box.copy` | 0 | PARTIAL | `box.copy` · полный контракт не подтверждён · вызов через объект |
| `box.delete` | 0 | PARTIAL | `box.delete` · полный контракт не подтверждён · вызов через объект |
| `box.get_bottom` | 0 | PARTIAL | `box.get_bottom` · полный контракт не подтверждён · вызов через объект |
| `box.get_left` | 0 | PARTIAL | `box.get_left` · полный контракт не подтверждён · вызов через объект |
| `box.get_right` | 0 | PARTIAL | `box.get_right` · полный контракт не подтверждён · вызов через объект |
| `box.get_top` | 0 | PARTIAL | `box.get_top` · полный контракт не подтверждён · вызов через объект |
| `box.new` | 0 | PARTIAL | `box.new_points_text` · xloc ограничен const · индикаторы и стратегии, координаты по допущенным профилям xloc |
| `box.new` | 1 | PARTIAL | `box.new_text` · xloc ограничен const · индикаторы и стратегии, координаты по допущенным профилям xloc |
| `box.set_bgcolor` | 0 | PARTIAL | `box.set_bgcolor` · полный контракт не подтверждён · вызов через объект |
| `box.set_border_color` | 0 | PARTIAL | `box.set_border_color` · полный контракт не подтверждён · вызов через объект |
| `box.set_border_style` | 0 | PARTIAL | `box.set_border_style` · полный контракт не подтверждён · вызов через объект |
| `box.set_border_width` | 0 | PARTIAL | `box.set_border_width` · полный контракт не подтверждён · вызов через объект |
| `box.set_bottom` | 0 | PARTIAL | `box.set_bottom` · полный контракт не подтверждён · вызов через объект |
| `box.set_bottom_right_point` | 0 | PARTIAL | `box.set_bottom_right_point` · полный контракт не подтверждён · вызов через объект |
| `box.set_extend` | 0 | PARTIAL | `box.set_extend` · полный контракт не подтверждён · вызов через объект |
| `box.set_left` | 0 | PARTIAL | `box.set_left` · полный контракт не подтверждён · вызов через объект |
| `box.set_lefttop` | 0 | PARTIAL | `box.set_lefttop` · полный контракт не подтверждён · вызов через объект |
| `box.set_right` | 0 | PARTIAL | `box.set_right` · полный контракт не подтверждён · вызов через объект |
| `box.set_rightbottom` | 0 | PARTIAL | `box.set_rightbottom` · полный контракт не подтверждён · вызов через объект |
| `box.set_text` | 0 | PARTIAL | `box.set_text` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_color` | 0 | PARTIAL | `box.set_text_color` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_font_family` | 0 | PARTIAL | `box.set_text_font_family` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_halign` | 0 | PARTIAL | `box.set_text_halign` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_size` | 0 | PARTIAL | `box.set_text_size` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_valign` | 0 | PARTIAL | `box.set_text_valign` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_wrap` | 0 | PARTIAL | `box.set_text_wrap` · полный контракт не подтверждён · вызов через объект |
| `box.set_top` | 0 | PARTIAL | `box.set_top` · полный контракт не подтверждён · вызов через объект |
| `box.set_top_left_point` | 0 | PARTIAL | `box.set_top_left_point` · полный контракт не подтверждён · вызов через объект |
| `chart.point.copy` | 0 | PARTIAL | `chart.point.copy` · полный контракт не подтверждён |
| `chart.point.from_index` | 0 | FULL | [chart.point.from_index](CHART_POINTS.md) |
| `chart.point.from_time` | 0 | FULL | [chart.point.from_time](CHART_POINTS.md) |
| `chart.point.new` | 0 | PARTIAL | `chart.point.new` · полный контракт не подтверждён |
| `chart.point.now` | 0 | PARTIAL | `chart.point.now` · полный контракт не подтверждён |
| `color` | 0 | PARTIAL | `colors.cast` · полный контракт не подтверждён |
| `color` | 1 | PARTIAL | `colors.cast` · полный контракт не подтверждён |
| `color` | 2 | PARTIAL | `colors.cast` · полный контракт не подтверждён |
| `color` | 3 | PARTIAL | `colors.cast` · полный контракт не подтверждён |
| `color.b` | 0 | PARTIAL | `colors.b` · полный контракт не подтверждён |
| `color.b` | 1 | PARTIAL | `colors.b` · полный контракт не подтверждён |
| `color.b` | 2 | PARTIAL | `colors.b` · полный контракт не подтверждён |
| `color.b` | 3 | PARTIAL | `colors.b` · полный контракт не подтверждён |
| `color.from_gradient` | 0 | PARTIAL | `colors.gradient` · полный контракт не подтверждён |
| `color.g` | 0 | PARTIAL | `colors.g` · полный контракт не подтверждён |
| `color.g` | 1 | PARTIAL | `colors.g` · полный контракт не подтверждён |
| `color.g` | 2 | PARTIAL | `colors.g` · полный контракт не подтверждён |
| `color.g` | 3 | PARTIAL | `colors.g` · полный контракт не подтверждён |
| `color.new` | 0 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `color.new` | 1 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `color.new` | 2 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `color.new` | 3 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `color.r` | 0 | PARTIAL | `colors.r` · полный контракт не подтверждён |
| `color.r` | 1 | PARTIAL | `colors.r` · полный контракт не подтверждён |
| `color.r` | 2 | PARTIAL | `colors.r` · полный контракт не подтверждён |
| `color.r` | 3 | PARTIAL | `colors.r` · полный контракт не подтверждён |
| `color.rgb` | 0 | PARTIAL | `colors.rgb` · полный контракт не подтверждён |
| `color.rgb` | 1 | PARTIAL | `colors.rgb` · полный контракт не подтверждён |
| `color.rgb` | 2 | PARTIAL | `colors.rgb` · полный контракт не подтверждён |
| `color.rgb` | 3 | PARTIAL | `colors.rgb` · полный контракт не подтверждён |
| `color.t` | 0 | PARTIAL | `colors.t` · полный контракт не подтверждён |
| `color.t` | 1 | PARTIAL | `colors.t` · полный контракт не подтверждён |
| `color.t` | 2 | PARTIAL | `colors.t` · полный контракт не подтверждён |
| `color.t` | 3 | PARTIAL | `colors.t` · полный контракт не подтверждён |
| `dayofmonth` | 0 | PARTIAL | `environment.dayofmonth` · полный контракт не подтверждён |
| `dayofmonth` | 1 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `dayofweek` | 0 | PARTIAL | `environment.dayofweek` · полный контракт не подтверждён |
| `dayofweek` | 1 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `fill` | 0 | PARTIAL | `bands.gradient_plot` · параметры вне профиля editable, transp · display ограничен display.all и display.none · маска из двух plot, первые шесть аргументов позиционные |
| `fill` | 1 | PARTIAL | `bands.fill_hline` · параметры вне профиля editable · тип transp в источнике описан неполно · display ограничен display.all и display.none · однотипные ссылки, первые три аргумента позиционные |
| `fill` | 2 | PARTIAL | `bands.fill_plot` · параметры вне профиля editable · тип transp в источнике описан неполно · display ограничен display.all и display.none · однотипные ссылки, первые три аргумента позиционные |
| `fixnan` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `fixnan` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `fixnan` | 2 | MISSING | нет совпадающего допуска для перегрузки |
| `fixnan` | 3 | MISSING | нет совпадающего допуска для перегрузки |
| `float` | 0 | PARTIAL | `numeric.float` · полный контракт не подтверждён |
| `float` | 1 | PARTIAL | `numeric.float` · полный контракт не подтверждён |
| `float` | 2 | PARTIAL | `numeric.float` · полный контракт не подтверждён |
| `float` | 3 | PARTIAL | `numeric.float` · полный контракт не подтверждён |
| `hline` | 0 | PARTIAL | `bands.hline` · параметры вне профиля editable · display ограничен display.all и display.none |
| `hour` | 0 | PARTIAL | `environment.hour` · полный контракт не подтверждён |
| `hour` | 1 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `indicator` | 0 | PARTIAL | `harness.indicator` · dynamic_requests по имени и позиции, const bool · [Профиль](DYNAMIC_REQUESTS.md) · параметры вне профиля max_bars_back, timeframe, timeframe_gaps, calc_bars_count · scale.left/right/none, scale.none только с overlay |
| `input` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 1 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 2 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 3 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 4 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 5 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.bool` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.color` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.enum` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.float` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.float` | 1 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.int` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.int` | 1 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.price` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `input.session` | 0 | PARTIAL | [глобальный параметр расписания](TRADING_SESSIONS.md) |
| `input.source` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.string` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.symbol` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.text_area` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `input.time` | 0 | PARTIAL | [константная дата, параметры запуска и поле UTC](DATE_INPUTS.md) |
| `input.timeframe` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `int` | 0 | PARTIAL | `numeric.int` · полный контракт не подтверждён |
| `int` | 1 | PARTIAL | `numeric.int` · полный контракт не подтверждён |
| `int` | 2 | PARTIAL | `numeric.int` · полный контракт не подтверждён |
| `int` | 3 | PARTIAL | `numeric.int` · полный контракт не подтверждён |
| `label` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `label.copy` | 0 | PARTIAL | `label.copy` · полный контракт не подтверждён · вызов через объект |
| `label.delete` | 0 | PARTIAL | `label.delete` · полный контракт не подтверждён · вызов через объект |
| `label.get_text` | 0 | PARTIAL | `label.get_text` · полный контракт не подтверждён · вызов через объект |
| `label.get_x` | 0 | PARTIAL | `label.get_x` · полный контракт не подтверждён · вызов через объект |
| `label.get_y` | 0 | PARTIAL | `label.get_y` · полный контракт не подтверждён · вызов через объект |
| `label.new` | 0 | PARTIAL | `label.new_points_text` · xloc ограничен const · индикаторы и стратегии, координаты по допущенным профилям xloc и yloc |
| `label.new` | 1 | PARTIAL | `label.new_text` · xloc ограничен const · индикаторы и стратегии, координаты по допущенным профилям xloc и yloc |
| `label.set_color` | 0 | PARTIAL | `label.set_color` · полный контракт не подтверждён · вызов через объект |
| `label.set_point` | 0 | PARTIAL | `label.set_point` · полный контракт не подтверждён · вызов через объект |
| `label.set_size` | 0 | PARTIAL | `label.set_size` · полный контракт не подтверждён · вызов через объект |
| `label.set_style` | 0 | PARTIAL | `label.set_style` · полный контракт не подтверждён · вызов через объект |
| `label.set_text` | 0 | PARTIAL | `label.set_text` · полный контракт не подтверждён · вызов через объект |
| `label.set_text_font_family` | 0 | PARTIAL | `label.set_text_font_family` · полный контракт не подтверждён · вызов через объект |
| `label.set_textalign` | 0 | PARTIAL | `label.set_textalign` · полный контракт не подтверждён · вызов через объект |
| `label.set_textcolor` | 0 | PARTIAL | `label.set_textcolor` · полный контракт не подтверждён · вызов через объект |
| `label.set_tooltip` | 0 | PARTIAL | `label.set_tooltip` · полный контракт не подтверждён · вызов через объект |
| `label.set_x` | 0 | PARTIAL | `label.set_x` · полный контракт не подтверждён · вызов через объект |
| `label.set_xloc` | 0 | PARTIAL | `label.set_xloc` · xloc ограничен const · вызов через объект |
| `label.set_xy` | 0 | PARTIAL | `label.set_xy` · полный контракт не подтверждён · вызов через объект |
| `label.set_y` | 0 | PARTIAL | `label.set_y` · полный контракт не подтверждён · вызов через объект |
| `label.set_yloc` | 0 | PARTIAL | `label.set_yloc` · полный контракт не подтверждён · вызов через объект |
| `library` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `line` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `line.copy` | 0 | PARTIAL | `line.copy` · полный контракт не подтверждён · вызов через объект |
| `line.delete` | 0 | PARTIAL | `line.delete` · полный контракт не подтверждён · вызов через объект |
| `line.get_price` | 0 | PARTIAL | `line.get_price` · полный контракт не подтверждён · вызов через объект |
| `line.get_x1` | 0 | PARTIAL | `line.get_x1` · полный контракт не подтверждён · вызов через объект |
| `line.get_x2` | 0 | PARTIAL | `line.get_x2` · полный контракт не подтверждён · вызов через объект |
| `line.get_y1` | 0 | PARTIAL | `line.get_y1` · полный контракт не подтверждён · вызов через объект |
| `line.get_y2` | 0 | PARTIAL | `line.get_y2` · полный контракт не подтверждён · вызов через объект |
| `line.new` | 0 | PARTIAL | `line.new_overlay` · xloc ограничен const · индикаторы и стратегии, закрытые xloc и стили, ширина 1–64 |
| `line.new` | 1 | PARTIAL | `line.new_points_overlay` · xloc ограничен const · индикаторы и стратегии, закрытые xloc и стили, ширина 1–64 |
| `line.set_color` | 0 | PARTIAL | `line.set_color` · полный контракт не подтверждён · вызов через объект |
| `line.set_extend` | 0 | PARTIAL | `line.set_extend` · полный контракт не подтверждён · вызов через объект |
| `line.set_first_point` | 0 | PARTIAL | `line.set_first_point` · полный контракт не подтверждён · вызов через объект |
| `line.set_second_point` | 0 | PARTIAL | `line.set_second_point` · полный контракт не подтверждён · вызов через объект |
| `line.set_style` | 0 | PARTIAL | `line.set_style` · полный контракт не подтверждён · вызов через объект |
| `line.set_width` | 0 | PARTIAL | `line.set_width` · полный контракт не подтверждён · вызов через объект |
| `line.set_x1` | 0 | PARTIAL | `line.set_x1` · полный контракт не подтверждён · вызов через объект |
| `line.set_x2` | 0 | PARTIAL | `line.set_x2` · полный контракт не подтверждён · вызов через объект |
| `line.set_xloc` | 0 | PARTIAL | `line.set_xloc` · xloc ограничен const · вызов через объект |
| `line.set_xy1` | 0 | PARTIAL | `line.set_xy1` · полный контракт не подтверждён · вызов через объект |
| `line.set_xy2` | 0 | PARTIAL | `line.set_xy2` · полный контракт не подтверждён · вызов через объект |
| `line.set_y1` | 0 | PARTIAL | `line.set_y1` · полный контракт не подтверждён · вызов через объект |
| `line.set_y2` | 0 | PARTIAL | `line.set_y2` · полный контракт не подтверждён · вызов через объект |
| `linefill` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `linefill.delete` | 0 | PARTIAL | `linefill.delete` · полный контракт не подтверждён · вызов через объект |
| `linefill.get_line1` | 0 | PARTIAL | `linefill.get_line1` · полный контракт не подтверждён · вызов через объект |
| `linefill.get_line2` | 0 | PARTIAL | `linefill.get_line2` · полный контракт не подтверждён · вызов через объект |
| `linefill.new` | 0 | PARTIAL | `linefill.new` · индикаторы и стратегии, заливка видна только в общей панели обеих линий |
| `linefill.set_color` | 0 | PARTIAL | `linefill.set_color` · полный контракт не подтверждён · вызов через объект |
| `log.error` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `log.error` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `log.info` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `log.info` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `log.warning` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `log.warning` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `map.clear` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.contains` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.copy` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.get` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.keys` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.new<type,type>` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.put` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.put_all` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.remove` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.size` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.values` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `math.abs` | 0 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 1 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 2 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 3 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 4 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 5 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 6 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 7 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.acos` | 0 | PARTIAL | `numeric.acos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.acos` | 1 | PARTIAL | `numeric.acos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.acos` | 2 | PARTIAL | `numeric.acos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.acos` | 3 | PARTIAL | `numeric.acos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.asin` | 0 | PARTIAL | `numeric.asin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.asin` | 1 | PARTIAL | `numeric.asin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.asin` | 2 | PARTIAL | `numeric.asin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.asin` | 3 | PARTIAL | `numeric.asin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.atan` | 0 | PARTIAL | `numeric.atan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.atan` | 1 | PARTIAL | `numeric.atan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.atan` | 2 | PARTIAL | `numeric.atan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.atan` | 3 | PARTIAL | `numeric.atan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.avg` | 0 | PARTIAL | `numeric.avg` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.avg` | 1 | PARTIAL | `numeric.avg` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.ceil` | 0 | PARTIAL | `numeric.ceil` · полный контракт не подтверждён |
| `math.ceil` | 1 | PARTIAL | `numeric.ceil` · полный контракт не подтверждён |
| `math.ceil` | 2 | PARTIAL | `numeric.ceil` · полный контракт не подтверждён |
| `math.ceil` | 3 | PARTIAL | `numeric.ceil` · полный контракт не подтверждён |
| `math.cos` | 0 | PARTIAL | `numeric.cos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.cos` | 1 | PARTIAL | `numeric.cos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.cos` | 2 | PARTIAL | `numeric.cos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.cos` | 3 | PARTIAL | `numeric.cos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.exp` | 0 | PARTIAL | `numeric.exp` · полный контракт не подтверждён |
| `math.exp` | 1 | PARTIAL | `numeric.exp` · полный контракт не подтверждён |
| `math.exp` | 2 | PARTIAL | `numeric.exp` · полный контракт не подтверждён |
| `math.exp` | 3 | PARTIAL | `numeric.exp` · полный контракт не подтверждён |
| `math.floor` | 0 | PARTIAL | `numeric.floor` · полный контракт не подтверждён |
| `math.floor` | 1 | PARTIAL | `numeric.floor` · полный контракт не подтверждён |
| `math.floor` | 2 | PARTIAL | `numeric.floor` · полный контракт не подтверждён |
| `math.floor` | 3 | PARTIAL | `numeric.floor` · полный контракт не подтверждён |
| `math.log` | 0 | PARTIAL | `numeric.log` · полный контракт не подтверждён |
| `math.log` | 1 | PARTIAL | `numeric.log` · полный контракт не подтверждён |
| `math.log` | 2 | PARTIAL | `numeric.log` · полный контракт не подтверждён |
| `math.log` | 3 | PARTIAL | `numeric.log` · полный контракт не подтверждён |
| `math.log10` | 0 | PARTIAL | `numeric.log10` · полный контракт не подтверждён |
| `math.log10` | 1 | PARTIAL | `numeric.log10` · полный контракт не подтверждён |
| `math.log10` | 2 | PARTIAL | `numeric.log10` · полный контракт не подтверждён |
| `math.log10` | 3 | PARTIAL | `numeric.log10` · полный контракт не подтверждён |
| `math.max` | 0 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 1 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 2 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 3 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 4 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 5 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 6 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 7 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 0 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 1 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 2 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 3 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 4 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 5 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 6 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 7 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.pow` | 0 | PARTIAL | `numeric.pow` · полный контракт не подтверждён |
| `math.pow` | 1 | PARTIAL | `numeric.pow` · полный контракт не подтверждён |
| `math.pow` | 2 | PARTIAL | `numeric.pow` · полный контракт не подтверждён |
| `math.pow` | 3 | PARTIAL | `numeric.pow` · полный контракт не подтверждён |
| `math.random` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `math.round` | 0 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 1 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 2 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 3 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 4 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 5 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 6 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 7 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round_to_mintick` | 0 | PARTIAL | `environment.round_to_mintick` · [шаг из снимка инструмента](TICK_PRICE.md) |
| `math.round_to_mintick` | 1 | PARTIAL | `environment.round_to_mintick` · [шаг из снимка инструмента](TICK_PRICE.md) |
| `math.sign` | 0 | PARTIAL | `numeric.sign` · полный контракт не подтверждён |
| `math.sign` | 1 | PARTIAL | `numeric.sign` · полный контракт не подтверждён |
| `math.sign` | 2 | PARTIAL | `numeric.sign` · полный контракт не подтверждён |
| `math.sign` | 3 | PARTIAL | `numeric.sign` · полный контракт не подтверждён |
| `math.sin` | 0 | PARTIAL | `numeric.sin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.sin` | 1 | PARTIAL | `numeric.sin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.sin` | 2 | PARTIAL | `numeric.sin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.sin` | 3 | PARTIAL | `numeric.sin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.sqrt` | 0 | PARTIAL | `numeric.sqrt` · полный контракт не подтверждён |
| `math.sqrt` | 1 | PARTIAL | `numeric.sqrt` · полный контракт не подтверждён |
| `math.sqrt` | 2 | PARTIAL | `numeric.sqrt` · полный контракт не подтверждён |
| `math.sqrt` | 3 | PARTIAL | `numeric.sqrt` · полный контракт не подтверждён |
| `math.sum` | 0 | PARTIAL | `window.sum` · полный контракт не подтверждён |
| `math.tan` | 0 | PARTIAL | `numeric.tan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.tan` | 1 | PARTIAL | `numeric.tan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.tan` | 2 | PARTIAL | `numeric.tan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.tan` | 3 | PARTIAL | `numeric.tan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.todegrees` | 0 | PARTIAL | `numeric.todegrees` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.toradians` | 0 | PARTIAL | `numeric.toradians` · [числовой профиль углов](TRIGONOMETRY.md) |
| `matrix.add_col` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.add_col` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.add_row` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.add_row` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.avg` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.avg` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.col` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.columns` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.concat` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.copy` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.det` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.det` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.diff` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.diff` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.eigenvalues` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.eigenvalues` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.eigenvectors` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.eigenvectors` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.elements_count` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.fill` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.get` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.inv` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.inv` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.is_antidiagonal` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_antisymmetric` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_binary` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_diagonal` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_identity` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_square` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_stochastic` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_symmetric` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_triangular` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_zero` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.kron` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.kron` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.max` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.max` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.median` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.median` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.min` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.min` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mode` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mode` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mult` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mult` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mult` | 2 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mult` | 3 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.new<type>` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.pinv` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.pinv` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.pow` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.pow` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.rank` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.remove_col` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.remove_row` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.reshape` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.reverse` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.row` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.rows` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.set` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.sort` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.submatrix` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.sum` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.sum` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.swap_columns` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.swap_rows` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.trace` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.trace` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.transpose` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `max_bars_back` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `minute` | 0 | PARTIAL | `environment.minute` · полный контракт не подтверждён |
| `minute` | 1 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `month` | 0 | PARTIAL | `environment.month` · полный контракт не подтверждён |
| `month` | 1 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `na` | 0 | PARTIAL | `numeric.na` · полный контракт не подтверждён |
| `na` | 1 | PARTIAL | `numeric.na` · тип x в источнике описан неполно |
| `nz` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `nz` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `nz` | 10 | PARTIAL | `bands.nz` · полный контракт не подтверждён |
| `nz` | 11 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `nz` | 12 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `nz` | 13 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `nz` | 14 | PARTIAL | `bands.nz` · полный контракт не подтверждён |
| `nz` | 15 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `nz` | 2 | MISSING | нет совпадающего допуска для перегрузки |
| `nz` | 3 | MISSING | нет совпадающего допуска для перегрузки |
| `nz` | 4 | MISSING | нет совпадающего допуска для перегрузки |
| `nz` | 5 | MISSING | нет совпадающего допуска для перегрузки |
| `nz` | 6 | MISSING | нет совпадающего допуска для перегрузки |
| `nz` | 7 | MISSING | нет совпадающего допуска для перегрузки |
| `nz` | 8 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `nz` | 9 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `plot` | 0 | PARTIAL | `colors.plot` · параметры вне профиля editable · тип transp в источнике описан неполно · offset ограничен input · четыре места вывода, константы и выбор через булев input · статические параметры оформления · статический offset в пределах ±10 000 |
| `plotarrow` | 0 | PARTIAL | `markers.plotarrow` · параметры вне профиля editable, transp · offset ограничен input · панель, статус и окно данных, константы и выбор через булев input · статический offset и высота 1–1000 пикселей |
| `plotbar` | 0 | PARTIAL | `ohlc.plotbar` · параметры вне профиля editable · четыре места вывода, константы и выбор через булев input · первые шесть аргументов позиционные |
| `plotcandle` | 0 | PARTIAL | `ohlc.plotcandle` · параметры вне профиля editable · четыре места вывода, константы и выбор через булев input · граница позиционных аргументов зависит от версии |
| `plotchar` | 0 | PARTIAL | `markers.plotchar` · параметры вне профиля editable · offset ограничен input · тип transp в источнике описан неполно · панель, статус и окно данных, константы и выбор через булев input |
| `plotshape` | 0 | PARTIAL | `markers.plotshape` · параметры вне профиля editable · offset ограничен input · тип transp в источнике описан неполно · панель, статус и окно данных, константы и выбор через булев input |
| `polyline.delete` | 0 | PARTIAL | `polyline.delete` · полный контракт не подтверждён · вызов через объект |
| `polyline.new` | 0 | PARTIAL | `polyline.new_styled` · curved ограничен const · xloc ограничен const · индикаторы и стратегии, только curved=false и прямые сегменты, ширина 1–64, лимит 512 вершин |
| `request.currency_rate` | 0 | PARTIAL | фиксированная пара · [дневные курсы](CURRENCY_RATES.md) |
| `request.dividends` | 0 | PARTIAL | фиксированный рынок, пересчёт по закреплённой паре · [дневные курсы](CURRENCY_RATES.md) |
| `request.earnings` | 0 | PARTIAL | фиксированный рынок, пересчёт по закреплённой паре · [дневные курсы](CURRENCY_RATES.md) |
| `request.economic` | 0 | PARTIAL | US/GDP и US/GDPQQ по публикациям · [макроданные](ECONOMIC_DATA.md) |
| `request.financial` | 0 | PARTIAL | четыре показателя отчётности · [периоды и публикации](FINANCIAL.md) |
| `request.quandl` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `request.security` | 0 | PARTIAL | `context.request` · часть типов expression вне профиля · gaps ограничен const · lookahead ограничен const · ignore_invalid_symbol ограничен const · currency ограничен const · calc_bars_count ограничен const · calc_bars_count по фактическим свечам, пересчёт цен по закреплённой валютной паре · [Профиль](SECURITY_CURRENCY.md) · до 32 рынков, вложенный выбор по свечам вызывающего рынка, первое обращение на истории · [Профиль](DYNAMIC_REQUESTS.md) · локальные массивы и плоский UDT внутри скалярной функции · [Примеры](CONTEXT_GRAPH.md) |
| `request.security_lower_tf` | 0 | PARTIAL | `context.lower_tf` · часть типов expression вне профиля · ignore_invalid_symbol ограничен const · currency ограничен const · ignore_invalid_timeframe ограничен const · calc_bars_count ограничен const · na-ссылка для неизвестного символа, пересчёт цены на закрытии каждой младшей свечи · [Профиль](SECURITY_CURRENCY.md) · до 32 рынков, вложенный выбор по свечам вызывающего рынка, первое обращение на истории · [Профиль](DYNAMIC_REQUESTS.md) |
| `request.seed` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `request.splits` | 0 | PARTIAL | фиксированный рынок · [корпоративные события](CORPORATE_ACTIONS.md) |
| `runtime.error` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `second` | 0 | PARTIAL | `environment.second` · полный контракт не подтверждён |
| `second` | 1 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `str.contains` | 0 | PARTIAL | `scalar.contains` · полный контракт не подтверждён |
| `str.contains` | 1 | PARTIAL | `scalar.contains` · полный контракт не подтверждён |
| `str.contains` | 2 | PARTIAL | `scalar.contains` · полный контракт не подтверждён |
| `str.endswith` | 0 | PARTIAL | `scalar.endswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.endswith` | 1 | PARTIAL | `scalar.endswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.endswith` | 2 | PARTIAL | `scalar.endswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.format` | 0 | PARTIAL | `strings.format` · скаляры и числовые форматы · [Профиль](STRING_FORMAT.md) |
| `str.format` | 1 | PARTIAL | `strings.format` · скаляры и числовые форматы · [Профиль](STRING_FORMAT.md) |
| `str.format_time` | 0 | PARTIAL | `strings.format_time` · календарные маски и закреплённые пояса · [Профиль](TIME_FORMAT.md) |
| `str.length` | 0 | PARTIAL | `scalar.length` · полный контракт не подтверждён |
| `str.length` | 1 | PARTIAL | `scalar.length` · полный контракт не подтверждён |
| `str.length` | 2 | PARTIAL | `scalar.length` · полный контракт не подтверждён |
| `str.lower` | 0 | PARTIAL | `scalar.lower` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.lower` | 1 | PARTIAL | `scalar.lower` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.lower` | 2 | PARTIAL | `scalar.lower` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.match` | 0 | PARTIAL | `scalar.match` · ограниченный синтаксис regex и бюджет поиска · [Профиль](STRING_MATCH.md) |
| `str.match` | 1 | PARTIAL | `scalar.match` · ограниченный синтаксис regex и бюджет поиска · [Профиль](STRING_MATCH.md) |
| `str.pos` | 0 | PARTIAL | `scalar.pos` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.pos` | 1 | PARTIAL | `scalar.pos` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.pos` | 2 | PARTIAL | `scalar.pos` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.repeat` | 0 | PARTIAL | `scalar.repeat` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.repeat` | 1 | PARTIAL | `scalar.repeat` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.repeat` | 2 | PARTIAL | `scalar.repeat` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.repeat` | 3 | PARTIAL | `scalar.repeat` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.replace` | 0 | PARTIAL | `scalar.replace` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.replace` | 1 | PARTIAL | `scalar.replace` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.replace` | 2 | PARTIAL | `scalar.replace` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.replace_all` | 0 | PARTIAL | `scalar.replace_all` · полный контракт не подтверждён |
| `str.replace_all` | 1 | PARTIAL | `scalar.replace_all` · полный контракт не подтверждён |
| `str.split` | 0 | PARTIAL | [массивы строк, объединение чисел, ограничения размера и работы](ARRAY_TEXT.md) |
| `str.startswith` | 0 | PARTIAL | `scalar.startswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.startswith` | 1 | PARTIAL | `scalar.startswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.startswith` | 2 | PARTIAL | `scalar.startswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.substring` | 0 | PARTIAL | `scalar.substring` · полный контракт не подтверждён |
| `str.substring` | 1 | PARTIAL | `scalar.substring` · полный контракт не подтверждён |
| `str.substring` | 2 | PARTIAL | `scalar.substring` · полный контракт не подтверждён |
| `str.substring` | 3 | PARTIAL | `scalar.substring.end` · полный контракт не подтверждён |
| `str.substring` | 4 | PARTIAL | `scalar.substring.end` · полный контракт не подтверждён |
| `str.substring` | 5 | PARTIAL | `scalar.substring.end` · полный контракт не подтверждён |
| `str.tonumber` | 0 | PARTIAL | `scalar.tonumber` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.tonumber` | 1 | PARTIAL | `scalar.tonumber` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.tonumber` | 2 | PARTIAL | `scalar.tonumber` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.tonumber` | 3 | PARTIAL | `scalar.tonumber` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.tostring` | 0 | PARTIAL | `scalar.tostring` · часть типов value вне профиля |
| `str.tostring` | 1 | PARTIAL | `scalar.tostring` · полный контракт не подтверждён |
| `str.tostring` | 2 | PARTIAL | `scalar.tostring.format` · часть типов value вне профиля · [форматирование по шагу инструмента](TICK_PRICE.md) |
| `str.tostring` | 3 | PARTIAL | `scalar.tostring.format` · [форматирование по шагу инструмента](TICK_PRICE.md) |
| `str.trim` | 0 | PARTIAL | `scalar.trim` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.trim` | 1 | PARTIAL | `scalar.trim` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.trim` | 2 | PARTIAL | `scalar.trim` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.trim` | 3 | PARTIAL | `scalar.trim` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.upper` | 0 | PARTIAL | `scalar.upper` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.upper` | 1 | PARTIAL | `scalar.upper` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.upper` | 2 | PARTIAL | `scalar.upper` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `strategy` | 0 | PARTIAL | `simulation.strategy_magnifier` · dynamic_requests по имени и позиции, const bool · [Профиль](DYNAMIC_REQUESTS.md) · параметры вне профиля shorttitle, max_bars_back, backtest_fill_limits_assumption, currency, margin_long, margin_short, calc_bars_count, risk_free_rate, fill_orders_on_standard_ohlc · scale.left/right/none, scale.none только с overlay |
| `strategy.cancel` | 0 | PARTIAL | `simulation.cancel` · id ограничен const |
| `strategy.cancel_all` | 0 | PARTIAL | `simulation.cancel_all` · полный контракт не подтверждён |
| `strategy.close` | 0 | PARTIAL | `simulation.close_partial` · параметры вне профиля comment, immediately · id ограничен const |
| `strategy.close_all` | 0 | PARTIAL | `simulation.close_all` · параметры вне профиля comment |
| `strategy.close_all` | 1 | PARTIAL | `simulation.close_all` · параметры вне профиля comment, immediately |
| `strategy.closedtrades.commission` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.entry_bar_index` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.entry_comment` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.entry_id` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.entry_price` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.entry_time` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.exit_bar_index` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.exit_comment` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.exit_id` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.exit_price` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.exit_time` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.max_drawdown` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.max_drawdown_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.max_runup` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.max_runup_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.profit` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.profit_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.size` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.convert_to_account` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.convert_to_symbol` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.default_entry_qty` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.entry` | 0 | PARTIAL | `simulation.entry_group` · параметры вне профиля comment · id ограничен const · direction ограничен const · oca_name ограничен const · oca_type ограничен const |
| `strategy.exit` | 0 | PARTIAL | `simulation.exit_extended` · параметры вне профиля comment, comment_profit, comment_loss, comment_trailing · id ограничен const · from_entry ограничен const · oca_name ограничен const |
| `strategy.opentrades.commission` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.entry_bar_index` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.entry_comment` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.entry_id` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.entry_price` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.entry_time` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.max_drawdown` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.max_drawdown_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.max_runup` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.max_runup_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.profit` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.profit_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.size` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.order` | 0 | PARTIAL | `simulation.order` · параметры вне профиля comment · id ограничен const · direction ограничен const · oca_name ограничен const · oca_type ограничен const |
| `strategy.risk.allow_entry_in` | 0 | PARTIAL | `simulation.risk_allow_entry_in` · value ограничен const |
| `strategy.risk.max_cons_loss_days` | 0 | PARTIAL | `simulation.risk_max_cons_loss_days` · count ограничен const · alert_message ограничен const |
| `strategy.risk.max_drawdown` | 0 | PARTIAL | `simulation.risk_max_drawdown` · value ограничен const · type ограничен const · alert_message ограничен const |
| `strategy.risk.max_intraday_filled_orders` | 0 | PARTIAL | `simulation.risk_max_intraday_filled_orders` · count ограничен const · alert_message ограничен const |
| `strategy.risk.max_intraday_loss` | 0 | PARTIAL | `simulation.risk_max_intraday_loss` · value ограничен const · type ограничен const · alert_message ограничен const |
| `strategy.risk.max_position_size` | 0 | PARTIAL | `simulation.risk_max_position_size` · contracts ограничен const |
| `string` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `string` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `string` | 2 | MISSING | нет совпадающего допуска для перегрузки |
| `string` | 3 | MISSING | нет совпадающего допуска для перегрузки |
| `syminfo.prefix` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `syminfo.prefix` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `syminfo.ticker` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `syminfo.ticker` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `ta.alma` | 0 | PARTIAL | `weighted.alma` · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `ta.alma` | 1 | PARTIAL | `weighted.alma` · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `ta.atr` | 0 | PARTIAL | `series.atr` · полный контракт не подтверждён |
| `ta.barssince` | 0 | PARTIAL | `signals.barssince` · полный контракт не подтверждён |
| `ta.bb` | 0 | PARTIAL | `channel.bb` · длина series int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.bbw` | 0 | PARTIAL | `channel.bbw` · длина series int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.cci` | 0 | PARTIAL | `momentum.cci` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ta.change` | 0 | PARTIAL | `series.change` · [типизированный ряд и длина по умолчанию](CHANGE_VALUEWHEN.md) |
| `ta.change` | 1 | PARTIAL | `series.change` · [типизированный ряд и длина по умолчанию](CHANGE_VALUEWHEN.md) |
| `ta.change` | 2 | PARTIAL | `series.change` · [типизированный ряд и длина по умолчанию](CHANGE_VALUEWHEN.md) |
| `ta.change` | 3 | PARTIAL | `series.change` · [типизированный ряд и длина по умолчанию](CHANGE_VALUEWHEN.md) |
| `ta.change` | 4 | PARTIAL | `series.change` · [типизированный ряд и длина по умолчанию](CHANGE_VALUEWHEN.md) |
| `ta.change` | 5 | PARTIAL | `series.change` · [типизированный ряд и длина по умолчанию](CHANGE_VALUEWHEN.md) |
| `ta.cmo` | 0 | PARTIAL | `momentum.cmo` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ta.cog` | 0 | PARTIAL | `weighted.cog` · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `ta.correlation` | 0 | PARTIAL | `correlation.pearson` · окно определённых пар · [Профиль](CORRELATION.md) |
| `ta.cross` | 0 | PARTIAL | `series.cross` · полный контракт не подтверждён |
| `ta.crossover` | 0 | PARTIAL | `series.crossover` · полный контракт не подтверждён |
| `ta.crossunder` | 0 | PARTIAL | `series.crossunder` · полный контракт не подтверждён |
| `ta.cum` | 0 | PARTIAL | `signals.cum` · полный контракт не подтверждён |
| `ta.dev` | 0 | PARTIAL | `momentum.dev` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ta.dmi` | 0 | PARTIAL | `trend.dmi` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `ta.ema` | 0 | PARTIAL | `series.ema` · полный контракт не подтверждён |
| `ta.falling` | 0 | PARTIAL | `swindow.falling` · полный контракт не подтверждён |
| `ta.highest` | 0 | PARTIAL | `window.highest` · полный контракт не подтверждён |
| `ta.highest` | 1 | PARTIAL | `window.highest` · полный контракт не подтверждён |
| `ta.highestbars` | 0 | PARTIAL | `swindow.highestbars` · полный контракт не подтверждён |
| `ta.highestbars` | 1 | PARTIAL | `swindow.highestbars` · полный контракт не подтверждён |
| `ta.hma` | 0 | PARTIAL | `weighted.hma` · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `ta.kc` | 0 | PARTIAL | `channel.kc` · длина simple int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.kc` | 1 | PARTIAL | `channel.kc` · длина simple int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.kcw` | 0 | PARTIAL | `channel.kcw` · длина simple int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.kcw` | 1 | PARTIAL | `channel.kcw` · длина simple int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.linreg` | 0 | PARTIAL | `indicator_window.linreg` · длина series int 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `ta.lowest` | 0 | PARTIAL | `window.lowest` · полный контракт не подтверждён |
| `ta.lowest` | 1 | PARTIAL | `window.lowest` · полный контракт не подтверждён |
| `ta.lowestbars` | 0 | PARTIAL | `swindow.lowestbars` · полный контракт не подтверждён |
| `ta.lowestbars` | 1 | PARTIAL | `swindow.lowestbars` · полный контракт не подтверждён |
| `ta.macd` | 0 | PARTIAL | `series.macd` · полный контракт не подтверждён |
| `ta.max` | 0 | PARTIAL | `extrema.max` · [окна и накопительные экстремумы](EXTREMA.md) |
| `ta.median` | 0 | PARTIAL | `statistics.median` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.median` | 1 | PARTIAL | `statistics.median` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.mfi` | 0 | PARTIAL | `volume.mfi` · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `ta.min` | 0 | PARTIAL | `extrema.min` · [окна и накопительные экстремумы](EXTREMA.md) |
| `ta.mode` | 0 | PARTIAL | `statistics.mode` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.mode` | 1 | PARTIAL | `statistics.mode` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.mom` | 0 | PARTIAL | `swindow.mom` · полный контракт не подтверждён |
| `ta.percentile_linear_interpolation` | 0 | PARTIAL | `statistics.percentile_linear_interpolation` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.percentile_nearest_rank` | 0 | PARTIAL | `statistics.percentile_nearest_rank` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.percentrank` | 0 | PARTIAL | `statistics.percentrank` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.pivot_point_levels` | 0 | PARTIAL | `pivot.point_levels` · шесть формул по наблюдаемым OHLC · [Профиль](PIVOT_LEVELS.md) |
| `ta.pivothigh` | 0 | PARTIAL | `pivot.pivothigh` · целые значения плеч, границы по версии · [Профиль](PIVOTS.md) |
| `ta.pivothigh` | 1 | PARTIAL | `pivot.pivothigh` · целые значения плеч, границы по версии · [Профиль](PIVOTS.md) |
| `ta.pivotlow` | 0 | PARTIAL | `pivot.pivotlow` · целые значения плеч, границы по версии · [Профиль](PIVOTS.md) |
| `ta.pivotlow` | 1 | PARTIAL | `pivot.pivotlow` · целые значения плеч, границы по версии · [Профиль](PIVOTS.md) |
| `ta.range` | 0 | PARTIAL | `extrema.range` · [окна и накопительные экстремумы](EXTREMA.md) |
| `ta.range` | 1 | PARTIAL | `extrema.range` · [окна и накопительные экстремумы](EXTREMA.md) |
| `ta.rising` | 0 | PARTIAL | `swindow.rising` · полный контракт не подтверждён |
| `ta.rma` | 0 | PARTIAL | `series.rma` · полный контракт не подтверждён |
| `ta.roc` | 0 | PARTIAL | `swindow.roc` · полный контракт не подтверждён |
| `ta.rsi` | 0 | PARTIAL | `series.rsi` · полный контракт не подтверждён |
| `ta.sar` | 0 | PARTIAL | `trend.sar` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `ta.sma` | 0 | PARTIAL | `series.sma` · полный контракт не подтверждён |
| `ta.stdev` | 0 | PARTIAL | `window.stdev` · полный контракт не подтверждён |
| `ta.stoch` | 0 | PARTIAL | `indicator_window.stoch` · длина series int 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `ta.supertrend` | 0 | PARTIAL | `trend.supertrend` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `ta.swma` | 0 | PARTIAL | `indicator_window.swma` · четыре отсчёта, [границы модели](WINDOW_INDICATORS.md) |
| `ta.tr` | 0 | PARTIAL | `series.tr` · полный контракт не подтверждён |
| `ta.tsi` | 0 | PARTIAL | `momentum.tsi` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ta.valuewhen` | 0 | PARTIAL | `signals.valuewhen` · полный контракт не подтверждён |
| `ta.valuewhen` | 1 | PARTIAL | `signals.valuewhen` · полный контракт не подтверждён |
| `ta.valuewhen` | 2 | PARTIAL | `signals.valuewhen` · полный контракт не подтверждён |
| `ta.valuewhen` | 3 | PARTIAL | `signals.valuewhen` · [цвет события и checkpoint](CHANGE_VALUEWHEN.md) |
| `ta.variance` | 0 | PARTIAL | `window.variance` · полный контракт не подтверждён |
| `ta.vwap` | 0 | PARTIAL | `volume.vwap_daily` · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `ta.vwap` | 1 | PARTIAL | `volume.vwap_anchor` · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `ta.vwap` | 2 | PARTIAL | `volume.vwap_bands` · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `ta.vwma` | 0 | PARTIAL | `swindow.vwma` · полный контракт не подтверждён |
| `ta.wma` | 0 | PARTIAL | `window.wma` · полный контракт не подтверждён |
| `ta.wpr` | 0 | PARTIAL | `momentum.wpr` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `table` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `table.cell` | 0 | PARTIAL | `table.cell_layout_font` · размеры 0–100% и типографика с границами по версии · вызов через объект |
| `table.cell_set_bgcolor` | 0 | PARTIAL | `table.cell_set_bgcolor` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_height` | 0 | PARTIAL | `table.cell_set_height` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text` | 0 | PARTIAL | `table.cell_set_text` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_color` | 0 | PARTIAL | `table.cell_set_text_color` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_font_family` | 0 | PARTIAL | `table.cell_set_text_font_family` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_halign` | 0 | PARTIAL | `table.cell_set_text_halign` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_size` | 0 | PARTIAL | `table.cell_set_text_size` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_valign` | 0 | PARTIAL | `table.cell_set_text_valign` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_tooltip` | 0 | PARTIAL | `table.cell_set_tooltip` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_width` | 0 | PARTIAL | `table.cell_set_width` · полный контракт не подтверждён · вызов через объект |
| `table.clear` | 0 | PARTIAL | `table.clear` · полный контракт не подтверждён · вызов через объект |
| `table.delete` | 0 | PARTIAL | `table.delete` · полный контракт не подтверждён · вызов через объект |
| `table.merge_cells` | 0 | PARTIAL | `table.merge_cells` · прямоугольники без пересечений, сохранение диапазона при очистке · вызов через объект |
| `table.new` | 0 | PARTIAL | `table.new_styled_overlay` · индикаторы и стратегии, девять позиций, 256 ячеек, именованные frame_color, frame_width, border_color, border_width |
| `table.set_bgcolor` | 0 | PARTIAL | `table.set_bgcolor` · полный контракт не подтверждён · вызов через объект |
| `table.set_border_color` | 0 | PARTIAL | `table.set_border_color` · полный контракт не подтверждён · вызов через объект |
| `table.set_border_width` | 0 | PARTIAL | `table.set_border_width` · полный контракт не подтверждён · вызов через объект |
| `table.set_frame_color` | 0 | PARTIAL | `table.set_frame_color` · полный контракт не подтверждён · вызов через объект |
| `table.set_frame_width` | 0 | PARTIAL | `table.set_frame_width` · полный контракт не подтверждён · вызов через объект |
| `table.set_position` | 0 | PARTIAL | `table.set_position` · полный контракт не подтверждён · вызов через объект |
| `ticker.heikinashi` | 0 | PARTIAL | фиксированный тикер, исторический security · [Профиль](HEIKIN_ASHI.md) |
| `ticker.inherit` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `ticker.kagi` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.linebreak` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.modify` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `ticker.new` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `ticker.pointfigure` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.renko` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.standard` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `time` | 0 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `time` | 1 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `time` | 2 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `time_close` | 0 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `time_close` | 1 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `time_close` | 2 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `timeframe.change` | 0 | PARTIAL | `environment.timeframe_change` · календарь UTC 24×7 · [Профиль](PIVOT_LEVELS.md) |
| `timeframe.from_seconds` | 0 | PARTIAL | `scalar.from_seconds` · полный контракт не подтверждён |
| `timeframe.from_seconds` | 1 | PARTIAL | `scalar.from_seconds` · полный контракт не подтверждён |
| `timeframe.in_seconds` | 0 | PARTIAL | `scalar.in_seconds` · полный контракт не подтверждён |
| `timeframe.in_seconds` | 1 | PARTIAL | `scalar.in_seconds` · полный контракт не подтверждён |
| `timestamp` | 0 | PARTIAL | константная дата и фиксированные смещения · [Профиль](DATE_INPUTS.md) |
| `timestamp` | 1 | PARTIAL | `environment.timestamp6` · полный контракт не подтверждён |
| `timestamp` | 2 | PARTIAL | `environment.timestamp6` · полный контракт не подтверждён |
| `timestamp` | 3 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `timestamp` | 4 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `weekofyear` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `weekofyear` | 1 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `year` | 0 | PARTIAL | `environment.year` · полный контракт не подтверждён |
| `year` | 1 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |

## Pine v6

| Функция | Перегрузка | Статус | Граница |
| --- | ---: | --- | --- |
| `alert` | 0 | PARTIAL | `alerts.call` · локальные события replay, три режима частоты и ограниченное сообщение · [Профиль](ALERTS.md) |
| `alertcondition` | 0 | PARTIAL | `alerts.condition` · глобальные условия, журнал replay, подстановки в отдельном профиле · [Профиль](ALERTS.md) |
| `array.abs` | 0 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.abs` | 1 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.avg` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.avg` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.binary_search` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.binary_search` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.binary_search_leftmost` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.binary_search_leftmost` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.binary_search_rightmost` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.binary_search_rightmost` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.clear` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.concat` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.copy` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.covariance` | 0 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.every` | 0 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.fill` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.first` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.from` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 10 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 11 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 2 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 3 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 4 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 5 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 6 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 7 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 8 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.from` | 9 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.get` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.includes` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.indexof` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.insert` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.join` | 0 | PARTIAL | [массивы строк, объединение чисел, ограничения размера и работы](ARRAY_TEXT.md) |
| `array.last` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.lastindexof` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.max` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.max` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.median` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.median` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.min` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.min` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.mode` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.mode` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.new<type>` | 0 | PARTIAL | [закрытый набор типов, в том числе графические ID](ARRAYS.md) |
| `array.new_bool` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_box` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.new_color` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_float` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_int` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_label` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.new_line` | 0 | PARTIAL | [массивы графических ID и действующие лимиты](DRAWING_ARRAYS.md) |
| `array.new_linefill` | 0 | PARTIAL | [изменяемый массив ссылок, независимые ячейки и восстановление](DRAWING_REFERENCE_ARRAYS.md) |
| `array.new_string` | 0 | PARTIAL | [закрытый набор типов элементов и лимиты массива](ARRAYS.md) |
| `array.new_table` | 0 | PARTIAL | [изменяемый массив ссылок, независимые ячейки и восстановление](DRAWING_REFERENCE_ARRAYS.md) |
| `array.percentile_linear_interpolation` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentile_linear_interpolation` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentile_nearest_rank` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentile_nearest_rank` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentrank` | 0 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.percentrank` | 1 | PARTIAL | [порядковые статистики int/float, тип результата и пропуски](ARRAY_STATISTICS.md) |
| `array.pop` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.push` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.range` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.range` | 1 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.remove` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.reverse` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.set` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.shift` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.size` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.slice` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.some` | 0 | PARTIAL | [парные наблюдения, абсолютные значения и условия в массивах](ARRAY_STATISTICS.md) |
| `array.sort` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.sort` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.sort_indices` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.sort_indices` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.standardize` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.standardize` | 1 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.stdev` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.stdev` | 1 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.sum` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.sum` | 1 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.unshift` | 0 | PARTIAL | [закрытый набор типов элементов, операций и лимитов](ARRAYS.md) |
| `array.variance` | 0 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `array.variance` | 1 | PARTIAL | [массивы int/float, пропуски и версионный делитель](ARRAY_STATISTICS.md) |
| `barcolor` | 0 | PARTIAL | `markers.barcolor` · параметры вне профиля editable · offset ограничен input · display ограничен display.all и display.none |
| `bgcolor` | 0 | PARTIAL | `markers.bgcolor` · параметры вне профиля editable · offset ограничен input · display ограничен display.all и display.none |
| `bool` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `bool` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `bool` | 2 | MISSING | нет совпадающего допуска для перегрузки |
| `bool` | 3 | MISSING | нет совпадающего допуска для перегрузки |
| `box` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `box.copy` | 0 | PARTIAL | `box.copy` · полный контракт не подтверждён · вызов через объект |
| `box.delete` | 0 | PARTIAL | `box.delete` · полный контракт не подтверждён · вызов через объект |
| `box.get_bottom` | 0 | PARTIAL | `box.get_bottom` · полный контракт не подтверждён · вызов через объект |
| `box.get_left` | 0 | PARTIAL | `box.get_left` · полный контракт не подтверждён · вызов через объект |
| `box.get_right` | 0 | PARTIAL | `box.get_right` · полный контракт не подтверждён · вызов через объект |
| `box.get_top` | 0 | PARTIAL | `box.get_top` · полный контракт не подтверждён · вызов через объект |
| `box.new` | 0 | PARTIAL | `box.new_points_text` · xloc ограничен const · индикаторы и стратегии, координаты по допущенным профилям xloc |
| `box.new` | 1 | PARTIAL | `box.new_text` · xloc ограничен const · индикаторы и стратегии, координаты по допущенным профилям xloc |
| `box.set_bgcolor` | 0 | PARTIAL | `box.set_bgcolor` · полный контракт не подтверждён · вызов через объект |
| `box.set_border_color` | 0 | PARTIAL | `box.set_border_color` · полный контракт не подтверждён · вызов через объект |
| `box.set_border_style` | 0 | PARTIAL | `box.set_border_style` · полный контракт не подтверждён · вызов через объект |
| `box.set_border_width` | 0 | PARTIAL | `box.set_border_width` · полный контракт не подтверждён · вызов через объект |
| `box.set_bottom` | 0 | PARTIAL | `box.set_bottom` · полный контракт не подтверждён · вызов через объект |
| `box.set_bottom_right_point` | 0 | PARTIAL | `box.set_bottom_right_point` · полный контракт не подтверждён · вызов через объект |
| `box.set_extend` | 0 | PARTIAL | `box.set_extend` · полный контракт не подтверждён · вызов через объект |
| `box.set_left` | 0 | PARTIAL | `box.set_left` · полный контракт не подтверждён · вызов через объект |
| `box.set_lefttop` | 0 | PARTIAL | `box.set_lefttop` · полный контракт не подтверждён · вызов через объект |
| `box.set_right` | 0 | PARTIAL | `box.set_right` · полный контракт не подтверждён · вызов через объект |
| `box.set_rightbottom` | 0 | PARTIAL | `box.set_rightbottom` · полный контракт не подтверждён · вызов через объект |
| `box.set_text` | 0 | PARTIAL | `box.set_text` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_color` | 0 | PARTIAL | `box.set_text_color` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_font_family` | 0 | PARTIAL | `box.set_text_font_family` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_formatting` | 0 | PARTIAL | `box.set_text_formatting` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_halign` | 0 | PARTIAL | `box.set_text_halign` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_size` | 0 | PARTIAL | `box.set_text_size` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_valign` | 0 | PARTIAL | `box.set_text_valign` · полный контракт не подтверждён · вызов через объект |
| `box.set_text_wrap` | 0 | PARTIAL | `box.set_text_wrap` · полный контракт не подтверждён · вызов через объект |
| `box.set_top` | 0 | PARTIAL | `box.set_top` · полный контракт не подтверждён · вызов через объект |
| `box.set_top_left_point` | 0 | PARTIAL | `box.set_top_left_point` · полный контракт не подтверждён · вызов через объект |
| `box.set_xloc` | 0 | PARTIAL | `box.set_xloc` · xloc ограничен const · вызов через объект |
| `chart.point.copy` | 0 | PARTIAL | `chart.point.copy` · полный контракт не подтверждён |
| `chart.point.from_index` | 0 | FULL | [chart.point.from_index](CHART_POINTS.md) |
| `chart.point.from_time` | 0 | FULL | [chart.point.from_time](CHART_POINTS.md) |
| `chart.point.new` | 0 | PARTIAL | `chart.point.new` · полный контракт не подтверждён |
| `chart.point.now` | 0 | PARTIAL | `chart.point.now` · полный контракт не подтверждён |
| `color` | 0 | PARTIAL | `colors.cast` · полный контракт не подтверждён |
| `color` | 1 | PARTIAL | `colors.cast` · полный контракт не подтверждён |
| `color` | 2 | PARTIAL | `colors.cast` · полный контракт не подтверждён |
| `color` | 3 | PARTIAL | `colors.cast` · полный контракт не подтверждён |
| `color.b` | 0 | PARTIAL | `colors.b` · полный контракт не подтверждён |
| `color.b` | 1 | PARTIAL | `colors.b` · полный контракт не подтверждён |
| `color.b` | 2 | PARTIAL | `colors.b` · полный контракт не подтверждён |
| `color.b` | 3 | PARTIAL | `colors.b` · полный контракт не подтверждён |
| `color.from_gradient` | 0 | PARTIAL | `colors.gradient` · полный контракт не подтверждён |
| `color.g` | 0 | PARTIAL | `colors.g` · полный контракт не подтверждён |
| `color.g` | 1 | PARTIAL | `colors.g` · полный контракт не подтверждён |
| `color.g` | 2 | PARTIAL | `colors.g` · полный контракт не подтверждён |
| `color.g` | 3 | PARTIAL | `colors.g` · полный контракт не подтверждён |
| `color.new` | 0 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `color.new` | 1 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `color.new` | 2 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `color.new` | 3 | PARTIAL | `colors.new` · полный контракт не подтверждён |
| `color.r` | 0 | PARTIAL | `colors.r` · полный контракт не подтверждён |
| `color.r` | 1 | PARTIAL | `colors.r` · полный контракт не подтверждён |
| `color.r` | 2 | PARTIAL | `colors.r` · полный контракт не подтверждён |
| `color.r` | 3 | PARTIAL | `colors.r` · полный контракт не подтверждён |
| `color.rgb` | 0 | PARTIAL | `colors.rgb` · полный контракт не подтверждён |
| `color.rgb` | 1 | PARTIAL | `colors.rgb` · полный контракт не подтверждён |
| `color.rgb` | 2 | PARTIAL | `colors.rgb` · полный контракт не подтверждён |
| `color.rgb` | 3 | PARTIAL | `colors.rgb` · полный контракт не подтверждён |
| `color.t` | 0 | PARTIAL | `colors.t` · полный контракт не подтверждён |
| `color.t` | 1 | PARTIAL | `colors.t` · полный контракт не подтверждён |
| `color.t` | 2 | PARTIAL | `colors.t` · полный контракт не подтверждён |
| `color.t` | 3 | PARTIAL | `colors.t` · полный контракт не подтверждён |
| `dayofmonth` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `dayofweek` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `fill` | 0 | PARTIAL | `bands.gradient_plot` · параметры вне профиля editable · display ограничен display.all и display.none · маска из двух plot, первые шесть аргументов позиционные |
| `fill` | 1 | PARTIAL | `bands.fill_hline` · параметры вне профиля editable · display ограничен display.all и display.none · однотипные ссылки, первые три аргумента позиционные |
| `fill` | 2 | PARTIAL | `bands.fill_plot` · параметры вне профиля editable · display ограничен display.all и display.none · однотипные ссылки, первые три аргумента позиционные |
| `fixnan` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `fixnan` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `fixnan` | 2 | MISSING | нет совпадающего допуска для перегрузки |
| `float` | 0 | PARTIAL | `numeric.float` · полный контракт не подтверждён |
| `float` | 1 | PARTIAL | `numeric.float` · полный контракт не подтверждён |
| `float` | 2 | PARTIAL | `numeric.float` · полный контракт не подтверждён |
| `float` | 3 | PARTIAL | `numeric.float` · полный контракт не подтверждён |
| `footprint.buy_volume` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `footprint.delta` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `footprint.get_row_by_price` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `footprint.poc` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `footprint.rows` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `footprint.sell_volume` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `footprint.total_volume` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `footprint.vah` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `footprint.val` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `hline` | 0 | PARTIAL | `bands.hline` · параметры вне профиля editable · display ограничен display.all и display.none |
| `hour` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `indicator` | 0 | PARTIAL | `harness.indicator` · dynamic_requests по имени и позиции, const bool · [Профиль](DYNAMIC_REQUESTS.md) · параметры вне профиля max_bars_back, timeframe, timeframe_gaps, calc_bars_count · scale.left/right/none, scale.none только с overlay |
| `input` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 1 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 2 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 3 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 4 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input` | 5 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.bool` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.color` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.enum` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.float` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.float` | 1 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.int` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.int` | 1 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.price` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `input.session` | 0 | PARTIAL | [глобальный параметр расписания](TRADING_SESSIONS.md) |
| `input.source` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.string` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.symbol` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `input.text_area` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `input.time` | 0 | PARTIAL | [константная дата, параметры запуска и поле UTC](DATE_INPUTS.md) |
| `input.timeframe` | 0 | PARTIAL | [прямое глобальное объявление, ограниченные defaults и метаданные](INPUTS.md) |
| `int` | 0 | PARTIAL | `numeric.int` · полный контракт не подтверждён |
| `int` | 1 | PARTIAL | `numeric.int` · полный контракт не подтверждён |
| `int` | 2 | PARTIAL | `numeric.int` · полный контракт не подтверждён |
| `int` | 3 | PARTIAL | `numeric.int` · полный контракт не подтверждён |
| `label` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `label.copy` | 0 | PARTIAL | `label.copy` · полный контракт не подтверждён · вызов через объект |
| `label.delete` | 0 | PARTIAL | `label.delete` · полный контракт не подтверждён · вызов через объект |
| `label.get_text` | 0 | PARTIAL | `label.get_text` · полный контракт не подтверждён · вызов через объект |
| `label.get_x` | 0 | PARTIAL | `label.get_x` · полный контракт не подтверждён · вызов через объект |
| `label.get_y` | 0 | PARTIAL | `label.get_y` · полный контракт не подтверждён · вызов через объект |
| `label.new` | 0 | PARTIAL | `label.new_points_text` · xloc ограничен const · индикаторы и стратегии, координаты по допущенным профилям xloc и yloc |
| `label.new` | 1 | PARTIAL | `label.new_text` · xloc ограничен const · индикаторы и стратегии, координаты по допущенным профилям xloc и yloc |
| `label.set_color` | 0 | PARTIAL | `label.set_color` · полный контракт не подтверждён · вызов через объект |
| `label.set_point` | 0 | PARTIAL | `label.set_point` · полный контракт не подтверждён · вызов через объект |
| `label.set_size` | 0 | PARTIAL | `label.set_size` · полный контракт не подтверждён · вызов через объект |
| `label.set_style` | 0 | PARTIAL | `label.set_style` · полный контракт не подтверждён · вызов через объект |
| `label.set_text` | 0 | PARTIAL | `label.set_text` · полный контракт не подтверждён · вызов через объект |
| `label.set_text_font_family` | 0 | PARTIAL | `label.set_text_font_family` · полный контракт не подтверждён · вызов через объект |
| `label.set_text_formatting` | 0 | PARTIAL | `label.set_text_formatting` · полный контракт не подтверждён · вызов через объект |
| `label.set_textalign` | 0 | PARTIAL | `label.set_textalign` · полный контракт не подтверждён · вызов через объект |
| `label.set_textcolor` | 0 | PARTIAL | `label.set_textcolor` · полный контракт не подтверждён · вызов через объект |
| `label.set_tooltip` | 0 | PARTIAL | `label.set_tooltip` · полный контракт не подтверждён · вызов через объект |
| `label.set_x` | 0 | PARTIAL | `label.set_x` · полный контракт не подтверждён · вызов через объект |
| `label.set_xloc` | 0 | PARTIAL | `label.set_xloc` · xloc ограничен const · вызов через объект |
| `label.set_xy` | 0 | PARTIAL | `label.set_xy` · полный контракт не подтверждён · вызов через объект |
| `label.set_y` | 0 | PARTIAL | `label.set_y` · полный контракт не подтверждён · вызов через объект |
| `label.set_yloc` | 0 | PARTIAL | `label.set_yloc` · полный контракт не подтверждён · вызов через объект |
| `library` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `line` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `line.copy` | 0 | PARTIAL | `line.copy` · полный контракт не подтверждён · вызов через объект |
| `line.delete` | 0 | PARTIAL | `line.delete` · полный контракт не подтверждён · вызов через объект |
| `line.get_price` | 0 | PARTIAL | `line.get_price` · полный контракт не подтверждён · вызов через объект |
| `line.get_x1` | 0 | PARTIAL | `line.get_x1` · полный контракт не подтверждён · вызов через объект |
| `line.get_x2` | 0 | PARTIAL | `line.get_x2` · полный контракт не подтверждён · вызов через объект |
| `line.get_y1` | 0 | PARTIAL | `line.get_y1` · полный контракт не подтверждён · вызов через объект |
| `line.get_y2` | 0 | PARTIAL | `line.get_y2` · полный контракт не подтверждён · вызов через объект |
| `line.new` | 0 | PARTIAL | `line.new_overlay` · xloc ограничен const · индикаторы и стратегии, закрытые xloc и стили, ширина 1–64 |
| `line.new` | 1 | PARTIAL | `line.new_points_overlay` · xloc ограничен const · индикаторы и стратегии, закрытые xloc и стили, ширина 1–64 |
| `line.set_color` | 0 | PARTIAL | `line.set_color` · полный контракт не подтверждён · вызов через объект |
| `line.set_extend` | 0 | PARTIAL | `line.set_extend` · полный контракт не подтверждён · вызов через объект |
| `line.set_first_point` | 0 | PARTIAL | `line.set_first_point` · полный контракт не подтверждён · вызов через объект |
| `line.set_second_point` | 0 | PARTIAL | `line.set_second_point` · полный контракт не подтверждён · вызов через объект |
| `line.set_style` | 0 | PARTIAL | `line.set_style` · полный контракт не подтверждён · вызов через объект |
| `line.set_width` | 0 | PARTIAL | `line.set_width` · полный контракт не подтверждён · вызов через объект |
| `line.set_x1` | 0 | PARTIAL | `line.set_x1` · полный контракт не подтверждён · вызов через объект |
| `line.set_x2` | 0 | PARTIAL | `line.set_x2` · полный контракт не подтверждён · вызов через объект |
| `line.set_xloc` | 0 | PARTIAL | `line.set_xloc` · xloc ограничен const · вызов через объект |
| `line.set_xy1` | 0 | PARTIAL | `line.set_xy1` · полный контракт не подтверждён · вызов через объект |
| `line.set_xy2` | 0 | PARTIAL | `line.set_xy2` · полный контракт не подтверждён · вызов через объект |
| `line.set_y1` | 0 | PARTIAL | `line.set_y1` · полный контракт не подтверждён · вызов через объект |
| `line.set_y2` | 0 | PARTIAL | `line.set_y2` · полный контракт не подтверждён · вызов через объект |
| `linefill` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `linefill.delete` | 0 | PARTIAL | `linefill.delete` · полный контракт не подтверждён · вызов через объект |
| `linefill.get_line1` | 0 | PARTIAL | `linefill.get_line1` · полный контракт не подтверждён · вызов через объект |
| `linefill.get_line2` | 0 | PARTIAL | `linefill.get_line2` · полный контракт не подтверждён · вызов через объект |
| `linefill.new` | 0 | PARTIAL | `linefill.new` · индикаторы и стратегии, заливка видна только в общей панели обеих линий |
| `linefill.set_color` | 0 | PARTIAL | `linefill.set_color` · полный контракт не подтверждён · вызов через объект |
| `log.error` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `log.error` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `log.info` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `log.info` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `log.warning` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `log.warning` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `map.clear` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.contains` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.copy` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.get` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.keys` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.new<type,type>` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.put` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.put_all` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.remove` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.size` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `map.values` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MAPS.md) |
| `math.abs` | 0 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 1 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 2 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 3 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 4 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 5 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 6 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.abs` | 7 | PARTIAL | `numeric.abs` · полный контракт не подтверждён |
| `math.acos` | 0 | PARTIAL | `numeric.acos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.acos` | 1 | PARTIAL | `numeric.acos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.acos` | 2 | PARTIAL | `numeric.acos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.acos` | 3 | PARTIAL | `numeric.acos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.asin` | 0 | PARTIAL | `numeric.asin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.asin` | 1 | PARTIAL | `numeric.asin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.asin` | 2 | PARTIAL | `numeric.asin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.asin` | 3 | PARTIAL | `numeric.asin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.atan` | 0 | PARTIAL | `numeric.atan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.atan` | 1 | PARTIAL | `numeric.atan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.atan` | 2 | PARTIAL | `numeric.atan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.atan` | 3 | PARTIAL | `numeric.atan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.avg` | 0 | PARTIAL | `numeric.avg` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.avg` | 1 | PARTIAL | `numeric.avg` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.ceil` | 0 | PARTIAL | `numeric.ceil` · полный контракт не подтверждён |
| `math.ceil` | 1 | PARTIAL | `numeric.ceil` · полный контракт не подтверждён |
| `math.ceil` | 2 | PARTIAL | `numeric.ceil` · полный контракт не подтверждён |
| `math.ceil` | 3 | PARTIAL | `numeric.ceil` · полный контракт не подтверждён |
| `math.cos` | 0 | PARTIAL | `numeric.cos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.cos` | 1 | PARTIAL | `numeric.cos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.cos` | 2 | PARTIAL | `numeric.cos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.cos` | 3 | PARTIAL | `numeric.cos` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.exp` | 0 | PARTIAL | `numeric.exp` · полный контракт не подтверждён |
| `math.exp` | 1 | PARTIAL | `numeric.exp` · полный контракт не подтверждён |
| `math.exp` | 2 | PARTIAL | `numeric.exp` · полный контракт не подтверждён |
| `math.exp` | 3 | PARTIAL | `numeric.exp` · полный контракт не подтверждён |
| `math.floor` | 0 | PARTIAL | `numeric.floor` · полный контракт не подтверждён |
| `math.floor` | 1 | PARTIAL | `numeric.floor` · полный контракт не подтверждён |
| `math.floor` | 2 | PARTIAL | `numeric.floor` · полный контракт не подтверждён |
| `math.floor` | 3 | PARTIAL | `numeric.floor` · полный контракт не подтверждён |
| `math.log` | 0 | PARTIAL | `numeric.log` · полный контракт не подтверждён |
| `math.log` | 1 | PARTIAL | `numeric.log` · полный контракт не подтверждён |
| `math.log` | 2 | PARTIAL | `numeric.log` · полный контракт не подтверждён |
| `math.log` | 3 | PARTIAL | `numeric.log` · полный контракт не подтверждён |
| `math.log10` | 0 | PARTIAL | `numeric.log10` · полный контракт не подтверждён |
| `math.log10` | 1 | PARTIAL | `numeric.log10` · полный контракт не подтверждён |
| `math.log10` | 2 | PARTIAL | `numeric.log10` · полный контракт не подтверждён |
| `math.log10` | 3 | PARTIAL | `numeric.log10` · полный контракт не подтверждён |
| `math.max` | 0 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 1 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 2 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 3 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 4 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 5 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 6 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.max` | 7 | PARTIAL | `numeric.max` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 0 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 1 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 2 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 3 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 4 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 5 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 6 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.min` | 7 | PARTIAL | `numeric.min` · 2–32 позиционных аргументов · [Профиль](NUMERIC_AGGREGATES.md) |
| `math.pow` | 0 | PARTIAL | `numeric.pow` · полный контракт не подтверждён |
| `math.pow` | 1 | PARTIAL | `numeric.pow` · полный контракт не подтверждён |
| `math.pow` | 2 | PARTIAL | `numeric.pow` · полный контракт не подтверждён |
| `math.pow` | 3 | PARTIAL | `numeric.pow` · полный контракт не подтверждён |
| `math.random` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `math.round` | 0 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 1 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 2 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 3 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 4 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 5 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 6 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round` | 7 | PARTIAL | `numeric.round` · полный контракт не подтверждён |
| `math.round_to_mintick` | 0 | PARTIAL | `environment.round_to_mintick` · [шаг из снимка инструмента](TICK_PRICE.md) |
| `math.round_to_mintick` | 1 | PARTIAL | `environment.round_to_mintick` · [шаг из снимка инструмента](TICK_PRICE.md) |
| `math.sign` | 0 | PARTIAL | `numeric.sign` · полный контракт не подтверждён |
| `math.sign` | 1 | PARTIAL | `numeric.sign` · полный контракт не подтверждён |
| `math.sign` | 2 | PARTIAL | `numeric.sign` · полный контракт не подтверждён |
| `math.sign` | 3 | PARTIAL | `numeric.sign` · полный контракт не подтверждён |
| `math.sin` | 0 | PARTIAL | `numeric.sin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.sin` | 1 | PARTIAL | `numeric.sin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.sin` | 2 | PARTIAL | `numeric.sin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.sin` | 3 | PARTIAL | `numeric.sin` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.sqrt` | 0 | PARTIAL | `numeric.sqrt` · полный контракт не подтверждён |
| `math.sqrt` | 1 | PARTIAL | `numeric.sqrt` · полный контракт не подтверждён |
| `math.sqrt` | 2 | PARTIAL | `numeric.sqrt` · полный контракт не подтверждён |
| `math.sqrt` | 3 | PARTIAL | `numeric.sqrt` · полный контракт не подтверждён |
| `math.sum` | 0 | PARTIAL | `window.sum` · полный контракт не подтверждён |
| `math.tan` | 0 | PARTIAL | `numeric.tan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.tan` | 1 | PARTIAL | `numeric.tan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.tan` | 2 | PARTIAL | `numeric.tan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.tan` | 3 | PARTIAL | `numeric.tan` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.todegrees` | 0 | PARTIAL | `numeric.todegrees` · [числовой профиль углов](TRIGONOMETRY.md) |
| `math.toradians` | 0 | PARTIAL | `numeric.toradians` · [числовой профиль углов](TRIGONOMETRY.md) |
| `matrix.add_col` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.add_row` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.avg` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.avg` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.col` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.columns` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.concat` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.copy` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.det` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.det` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.diff` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.diff` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.eigenvalues` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.eigenvalues` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.eigenvectors` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.eigenvectors` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.elements_count` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.fill` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.get` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.inv` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.inv` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.is_antidiagonal` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_antisymmetric` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_binary` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_diagonal` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_identity` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_square` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_stochastic` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_symmetric` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_triangular` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.is_zero` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.kron` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.kron` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.max` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.max` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.median` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.median` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.min` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.min` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mode` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mode` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mult` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mult` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mult` | 2 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.mult` | 3 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.new<type>` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.pinv` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.pinv` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `matrix.pow` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.pow` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.rank` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.remove_col` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.remove_row` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.reshape` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.reverse` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.row` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.rows` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.set` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.sort` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.sort` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.submatrix` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.sum` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.sum` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.swap_columns` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.swap_rows` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.trace` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.trace` | 1 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `matrix.transpose` | 0 | PARTIAL | [закрытый набор типов и лимиты коллекции](MATRICES.md) |
| `max_bars_back` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `minute` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `month` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `na` | 0 | PARTIAL | `numeric.na` · полный контракт не подтверждён |
| `na` | 1 | PARTIAL | `numeric.na` · тип x в источнике описан неполно |
| `nz` | 0 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `nz` | 1 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `nz` | 2 | PARTIAL | `bands.nz` · полный контракт не подтверждён |
| `nz` | 3 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `nz` | 4 | PARTIAL | `numeric.nz` · полный контракт не подтверждён |
| `nz` | 5 | PARTIAL | `bands.nz` · полный контракт не подтверждён |
| `plot` | 0 | PARTIAL | `colors.plot` · параметры вне профиля editable · offset ограничен input · четыре места вывода, константы и выбор через булев input · статические параметры оформления · статический offset в пределах ±10 000 |
| `plotarrow` | 0 | PARTIAL | `markers.plotarrow` · параметры вне профиля editable · offset ограничен input · панель, статус и окно данных, константы и выбор через булев input · статический offset и высота 1–1000 пикселей |
| `plotbar` | 0 | PARTIAL | `ohlc.plotbar` · параметры вне профиля editable · четыре места вывода, константы и выбор через булев input · первые шесть аргументов позиционные |
| `plotcandle` | 0 | PARTIAL | `ohlc.plotcandle` · параметры вне профиля editable · четыре места вывода, константы и выбор через булев input · граница позиционных аргументов зависит от версии |
| `plotchar` | 0 | PARTIAL | `markers.plotchar` · параметры вне профиля editable · offset ограничен input · панель, статус и окно данных, константы и выбор через булев input |
| `plotshape` | 0 | PARTIAL | `markers.plotshape` · параметры вне профиля editable · offset ограничен input · панель, статус и окно данных, константы и выбор через булев input |
| `polyline.delete` | 0 | PARTIAL | `polyline.delete` · полный контракт не подтверждён · вызов через объект |
| `polyline.new` | 0 | PARTIAL | `polyline.new_styled` · curved ограничен const · xloc ограничен const · индикаторы и стратегии, только curved=false и прямые сегменты, ширина 1–64, лимит 512 вершин |
| `request.currency_rate` | 0 | PARTIAL | фиксированная пара · [дневные курсы](CURRENCY_RATES.md) |
| `request.dividends` | 0 | PARTIAL | фиксированный рынок, пересчёт по закреплённой паре · [дневные курсы](CURRENCY_RATES.md) |
| `request.earnings` | 0 | PARTIAL | фиксированный рынок, пересчёт по закреплённой паре · [дневные курсы](CURRENCY_RATES.md) |
| `request.economic` | 0 | PARTIAL | US/GDP и US/GDPQQ по публикациям · [макроданные](ECONOMIC_DATA.md) |
| `request.financial` | 0 | PARTIAL | четыре показателя отчётности · [периоды и публикации](FINANCIAL.md) |
| `request.footprint` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `request.quandl` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `request.security` | 0 | PARTIAL | `context.request` · часть типов expression вне профиля · gaps ограничен const · lookahead ограничен const · ignore_invalid_symbol ограничен const · currency ограничен const · calc_bars_count ограничен const · calc_bars_count по фактическим свечам, пересчёт цен по закреплённой валютной паре · [Профиль](SECURITY_CURRENCY.md) · до 32 рынков, вложенный выбор по свечам вызывающего рынка, первое обращение на истории · [Профиль](DYNAMIC_REQUESTS.md) · локальные массивы и плоский UDT внутри скалярной функции · [Примеры](CONTEXT_GRAPH.md) |
| `request.security_lower_tf` | 0 | PARTIAL | `context.lower_tf` · часть типов expression вне профиля · ignore_invalid_symbol ограничен const · currency ограничен const · ignore_invalid_timeframe ограничен const · calc_bars_count ограничен const · na-ссылка для неизвестного символа, пересчёт цены на закрытии каждой младшей свечи · [Профиль](SECURITY_CURRENCY.md) · до 32 рынков, вложенный выбор по свечам вызывающего рынка, первое обращение на истории · [Профиль](DYNAMIC_REQUESTS.md) |
| `request.seed` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `request.splits` | 0 | PARTIAL | фиксированный рынок · [корпоративные события](CORPORATE_ACTIONS.md) |
| `runtime.error` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `second` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `str.contains` | 0 | PARTIAL | `scalar.contains` · полный контракт не подтверждён |
| `str.contains` | 1 | PARTIAL | `scalar.contains` · полный контракт не подтверждён |
| `str.contains` | 2 | PARTIAL | `scalar.contains` · полный контракт не подтверждён |
| `str.endswith` | 0 | PARTIAL | `scalar.endswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.endswith` | 1 | PARTIAL | `scalar.endswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.endswith` | 2 | PARTIAL | `scalar.endswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.format` | 0 | PARTIAL | `strings.format` · скаляры и числовые форматы · [Профиль](STRING_FORMAT.md) |
| `str.format` | 1 | PARTIAL | `strings.format` · скаляры и числовые форматы · [Профиль](STRING_FORMAT.md) |
| `str.format_time` | 0 | PARTIAL | `strings.format_time` · календарные маски и закреплённые пояса · [Профиль](TIME_FORMAT.md) |
| `str.length` | 0 | PARTIAL | `scalar.length` · полный контракт не подтверждён |
| `str.length` | 1 | PARTIAL | `scalar.length` · полный контракт не подтверждён |
| `str.length` | 2 | PARTIAL | `scalar.length` · полный контракт не подтверждён |
| `str.lower` | 0 | PARTIAL | `scalar.lower` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.lower` | 1 | PARTIAL | `scalar.lower` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.lower` | 2 | PARTIAL | `scalar.lower` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.match` | 0 | PARTIAL | `scalar.match` · ограниченный синтаксис regex и бюджет поиска · [Профиль](STRING_MATCH.md) |
| `str.match` | 1 | PARTIAL | `scalar.match` · ограниченный синтаксис regex и бюджет поиска · [Профиль](STRING_MATCH.md) |
| `str.pos` | 0 | PARTIAL | `scalar.pos` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.pos` | 1 | PARTIAL | `scalar.pos` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.pos` | 2 | PARTIAL | `scalar.pos` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.repeat` | 0 | PARTIAL | `scalar.repeat` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.repeat` | 1 | PARTIAL | `scalar.repeat` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.repeat` | 2 | PARTIAL | `scalar.repeat` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.repeat` | 3 | PARTIAL | `scalar.repeat` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.replace` | 0 | PARTIAL | `scalar.replace` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.replace` | 1 | PARTIAL | `scalar.replace` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.replace` | 2 | PARTIAL | `scalar.replace` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.replace_all` | 0 | PARTIAL | `scalar.replace_all` · полный контракт не подтверждён |
| `str.replace_all` | 1 | PARTIAL | `scalar.replace_all` · полный контракт не подтверждён |
| `str.split` | 0 | PARTIAL | [массивы строк, объединение чисел, ограничения размера и работы](ARRAY_TEXT.md) |
| `str.startswith` | 0 | PARTIAL | `scalar.startswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.startswith` | 1 | PARTIAL | `scalar.startswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.startswith` | 2 | PARTIAL | `scalar.startswith` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.substring` | 0 | PARTIAL | `scalar.substring.end` · полный контракт не подтверждён |
| `str.substring` | 1 | PARTIAL | `scalar.substring.end` · полный контракт не подтверждён |
| `str.substring` | 2 | PARTIAL | `scalar.substring.end` · полный контракт не подтверждён |
| `str.tonumber` | 0 | PARTIAL | `scalar.tonumber` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.tonumber` | 1 | PARTIAL | `scalar.tonumber` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.tonumber` | 2 | PARTIAL | `scalar.tonumber` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.tonumber` | 3 | PARTIAL | `scalar.tonumber` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.tostring` | 0 | PARTIAL | `scalar.tostring` · подпись enum · [версионный контракт](ENUMS.md) · часть типов value вне профиля |
| `str.tostring` | 1 | PARTIAL | `scalar.tostring` · подпись enum · [версионный контракт](ENUMS.md) |
| `str.tostring` | 2 | FULL | подпись константного члена enum · [Версии и квалификаторы](ENUMS.md) |
| `str.tostring` | 3 | PARTIAL | `scalar.tostring.format` · часть типов value вне профиля · [форматирование по шагу инструмента](TICK_PRICE.md) |
| `str.tostring` | 4 | PARTIAL | `scalar.tostring.format` · [форматирование по шагу инструмента](TICK_PRICE.md) |
| `str.trim` | 0 | PARTIAL | `scalar.trim` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.trim` | 1 | PARTIAL | `scalar.trim` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.trim` | 2 | PARTIAL | `scalar.trim` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.trim` | 3 | PARTIAL | `scalar.trim` · исторические бары, ограниченная обработка текста · [Формы чисел и пределы](PRICE_TEXT.md) |
| `str.upper` | 0 | PARTIAL | `scalar.upper` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.upper` | 1 | PARTIAL | `scalar.upper` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `str.upper` | 2 | PARTIAL | `scalar.upper` · Unicode, до 4096 кодовых точек, общий бюджет вычисления · [Строки](STRING_SEARCH.md) |
| `strategy` | 0 | PARTIAL | `simulation.strategy_magnifier` · dynamic_requests по имени и позиции, const bool · [Профиль](DYNAMIC_REQUESTS.md) · параметры вне профиля shorttitle, max_bars_back, backtest_fill_limits_assumption, currency, margin_long, margin_short, calc_bars_count, risk_free_rate, fill_orders_on_standard_ohlc, calc_on_every_history_tick · scale.left/right/none, scale.none только с overlay |
| `strategy.cancel` | 0 | PARTIAL | `simulation.cancel` · id ограничен const |
| `strategy.cancel_all` | 0 | PARTIAL | `simulation.cancel_all` · полный контракт не подтверждён |
| `strategy.close` | 0 | PARTIAL | `simulation.close_partial` · параметры вне профиля comment, immediately · id ограничен const |
| `strategy.close_all` | 0 | PARTIAL | `simulation.close_all` · параметры вне профиля comment, immediately |
| `strategy.closedtrades.commission` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.entry_bar_index` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.entry_comment` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.entry_id` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.entry_price` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.entry_time` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.exit_bar_index` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.exit_comment` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.exit_id` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.exit_price` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.exit_time` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.max_drawdown` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.max_drawdown_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.max_runup` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.max_runup_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.profit` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.closedtrades.profit_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.closedtrades.size` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.convert_to_account` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.convert_to_symbol` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.default_entry_qty` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.entry` | 0 | PARTIAL | `simulation.entry_group` · параметры вне профиля comment · id ограничен const · direction ограничен const · oca_name ограничен const · oca_type ограничен const |
| `strategy.exit` | 0 | PARTIAL | `simulation.exit_extended` · параметры вне профиля comment, comment_profit, comment_loss, comment_trailing · id ограничен const · from_entry ограничен const · oca_name ограничен const |
| `strategy.opentrades.commission` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.entry_bar_index` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.entry_comment` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.entry_id` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.entry_price` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.entry_time` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.max_drawdown` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.max_drawdown_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.max_runup` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.max_runup_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.profit` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.opentrades.profit_percent` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `strategy.opentrades.size` | 0 | PARTIAL | `trade.read` · полный контракт не подтверждён |
| `strategy.order` | 0 | PARTIAL | `simulation.order` · параметры вне профиля comment · id ограничен const · direction ограничен const · oca_name ограничен const · oca_type ограничен const |
| `strategy.risk.allow_entry_in` | 0 | PARTIAL | `simulation.risk_allow_entry_in` · value ограничен const |
| `strategy.risk.max_cons_loss_days` | 0 | PARTIAL | `simulation.risk_max_cons_loss_days` · count ограничен const · alert_message ограничен const |
| `strategy.risk.max_drawdown` | 0 | PARTIAL | `simulation.risk_max_drawdown` · value ограничен const · type ограничен const · alert_message ограничен const |
| `strategy.risk.max_intraday_filled_orders` | 0 | PARTIAL | `simulation.risk_max_intraday_filled_orders` · count ограничен const · alert_message ограничен const |
| `strategy.risk.max_intraday_loss` | 0 | PARTIAL | `simulation.risk_max_intraday_loss` · value ограничен const · type ограничен const · alert_message ограничен const |
| `strategy.risk.max_position_size` | 0 | PARTIAL | `simulation.risk_max_position_size` · contracts ограничен const |
| `string` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `string` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `string` | 2 | MISSING | нет совпадающего допуска для перегрузки |
| `string` | 3 | MISSING | нет совпадающего допуска для перегрузки |
| `syminfo.prefix` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `syminfo.prefix` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `syminfo.ticker` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `syminfo.ticker` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `ta.alma` | 0 | PARTIAL | `weighted.alma` · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `ta.atr` | 0 | PARTIAL | `series.atr` · полный контракт не подтверждён |
| `ta.barssince` | 0 | PARTIAL | `signals.barssince` · полный контракт не подтверждён |
| `ta.bb` | 0 | PARTIAL | `channel.bb` · длина series int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.bbw` | 0 | PARTIAL | `channel.bbw` · длина series int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.cci` | 0 | PARTIAL | `momentum.cci` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ta.change` | 0 | PARTIAL | `series.change` · [типизированный ряд и длина по умолчанию](CHANGE_VALUEWHEN.md) |
| `ta.change` | 1 | PARTIAL | `series.change` · [типизированный ряд и длина по умолчанию](CHANGE_VALUEWHEN.md) |
| `ta.change` | 2 | PARTIAL | `series.change` · [типизированный ряд и длина по умолчанию](CHANGE_VALUEWHEN.md) |
| `ta.cmo` | 0 | PARTIAL | `momentum.cmo` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ta.cog` | 0 | PARTIAL | `weighted.cog` · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `ta.correlation` | 0 | PARTIAL | `correlation.pearson` · окно определённых пар · [Профиль](CORRELATION.md) |
| `ta.cross` | 0 | PARTIAL | `series.cross` · полный контракт не подтверждён |
| `ta.crossover` | 0 | PARTIAL | `series.crossover` · полный контракт не подтверждён |
| `ta.crossunder` | 0 | PARTIAL | `series.crossunder` · полный контракт не подтверждён |
| `ta.cum` | 0 | PARTIAL | `signals.cum` · полный контракт не подтверждён |
| `ta.dev` | 0 | PARTIAL | `momentum.dev` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ta.dmi` | 0 | PARTIAL | `trend.dmi` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `ta.ema` | 0 | PARTIAL | `series.ema` · полный контракт не подтверждён |
| `ta.falling` | 0 | PARTIAL | `swindow.falling` · полный контракт не подтверждён |
| `ta.highest` | 0 | PARTIAL | `window.highest` · полный контракт не подтверждён |
| `ta.highestbars` | 0 | PARTIAL | `swindow.highestbars` · полный контракт не подтверждён |
| `ta.hma` | 0 | PARTIAL | `weighted.hma` · версионные окна и правила пропусков · [Профиль](WEIGHTED_INDICATORS.md) |
| `ta.kc` | 0 | PARTIAL | `channel.kc` · длина simple int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.kcw` | 0 | PARTIAL | `channel.kcw` · длина simple int 1–100 000 · [профиль](CHANNEL_INDICATORS.md) |
| `ta.linreg` | 0 | PARTIAL | `indicator_window.linreg` · длина series int 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `ta.lowest` | 0 | PARTIAL | `window.lowest` · полный контракт не подтверждён |
| `ta.lowestbars` | 0 | PARTIAL | `swindow.lowestbars` · полный контракт не подтверждён |
| `ta.macd` | 0 | PARTIAL | `series.macd` · полный контракт не подтверждён |
| `ta.max` | 0 | PARTIAL | `extrema.max` · [окна и накопительные экстремумы](EXTREMA.md) |
| `ta.median` | 0 | PARTIAL | `statistics.median` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.median` | 1 | PARTIAL | `statistics.median` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.mfi` | 0 | PARTIAL | `volume.mfi` · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `ta.min` | 0 | PARTIAL | `extrema.min` · [окна и накопительные экстремумы](EXTREMA.md) |
| `ta.mode` | 0 | PARTIAL | `statistics.mode` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.mode` | 1 | PARTIAL | `statistics.mode` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.mom` | 0 | PARTIAL | `swindow.mom` · полный контракт не подтверждён |
| `ta.percentile_linear_interpolation` | 0 | PARTIAL | `statistics.percentile_linear_interpolation` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.percentile_nearest_rank` | 0 | PARTIAL | `statistics.percentile_nearest_rank` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.percentrank` | 0 | PARTIAL | `statistics.percentrank` · [ранги, длины и пропуски](WINDOW_STATISTICS.md) |
| `ta.pivot_point_levels` | 0 | PARTIAL | `pivot.point_levels` · шесть формул по наблюдаемым OHLC · [Профиль](PIVOT_LEVELS.md) |
| `ta.pivothigh` | 0 | PARTIAL | `pivot.pivothigh` · целые значения плеч, границы по версии · [Профиль](PIVOTS.md) |
| `ta.pivothigh` | 1 | PARTIAL | `pivot.pivothigh` · целые значения плеч, границы по версии · [Профиль](PIVOTS.md) |
| `ta.pivotlow` | 0 | PARTIAL | `pivot.pivotlow` · целые значения плеч, границы по версии · [Профиль](PIVOTS.md) |
| `ta.pivotlow` | 1 | PARTIAL | `pivot.pivotlow` · целые значения плеч, границы по версии · [Профиль](PIVOTS.md) |
| `ta.range` | 0 | PARTIAL | `extrema.range` · [окна и накопительные экстремумы](EXTREMA.md) |
| `ta.range` | 1 | PARTIAL | `extrema.range` · [окна и накопительные экстремумы](EXTREMA.md) |
| `ta.rci` | 0 | PARTIAL | `correlation.rci` · фиксированная длина, средний ранг при равенстве · [Профиль](CORRELATION.md) |
| `ta.rising` | 0 | PARTIAL | `swindow.rising` · полный контракт не подтверждён |
| `ta.rma` | 0 | PARTIAL | `series.rma` · полный контракт не подтверждён |
| `ta.roc` | 0 | PARTIAL | `swindow.roc` · полный контракт не подтверждён |
| `ta.rsi` | 0 | PARTIAL | `series.rsi` · полный контракт не подтверждён |
| `ta.sar` | 0 | PARTIAL | `trend.sar` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `ta.sma` | 0 | PARTIAL | `series.sma` · полный контракт не подтверждён |
| `ta.stdev` | 0 | PARTIAL | `window.stdev` · полный контракт не подтверждён |
| `ta.stoch` | 0 | PARTIAL | `indicator_window.stoch` · длина series int 1–100 000, [границы модели](WINDOW_INDICATORS.md) |
| `ta.supertrend` | 0 | PARTIAL | `trend.supertrend` · [прогрев, развороты и допуски](TREND_INDICATORS.md) |
| `ta.swma` | 0 | PARTIAL | `indicator_window.swma` · четыре отсчёта, [границы модели](WINDOW_INDICATORS.md) |
| `ta.tr` | 0 | PARTIAL | `series.tr` · полный контракт не подтверждён |
| `ta.tsi` | 0 | PARTIAL | `momentum.tsi` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `ta.valuewhen` | 0 | PARTIAL | `signals.valuewhen` · полный контракт не подтверждён |
| `ta.valuewhen` | 1 | PARTIAL | `signals.valuewhen` · полный контракт не подтверждён |
| `ta.valuewhen` | 2 | PARTIAL | `signals.valuewhen` · полный контракт не подтверждён |
| `ta.valuewhen` | 3 | PARTIAL | `signals.valuewhen` · [цвет события и checkpoint](CHANGE_VALUEWHEN.md) |
| `ta.variance` | 0 | PARTIAL | `window.variance` · полный контракт не подтверждён |
| `ta.vwap` | 0 | PARTIAL | `volume.vwap_anchor` · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `ta.vwap` | 1 | PARTIAL | `volume.vwap_bands` · [правила объёма и якоря](VOLUME_INDICATORS.md) |
| `ta.vwma` | 0 | PARTIAL | `swindow.vwma` · полный контракт не подтверждён |
| `ta.wma` | 0 | PARTIAL | `window.wma` · полный контракт не подтверждён |
| `ta.wpr` | 0 | PARTIAL | `momentum.wpr` · [версии, длины и правила прогрева](MOMENTUM.md) |
| `table` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `table.cell` | 0 | PARTIAL | `table.cell_layout_format` · размеры 0–100% и типографика с границами по версии · вызов через объект |
| `table.cell_set_bgcolor` | 0 | PARTIAL | `table.cell_set_bgcolor` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_height` | 0 | PARTIAL | `table.cell_set_height` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text` | 0 | PARTIAL | `table.cell_set_text` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_color` | 0 | PARTIAL | `table.cell_set_text_color` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_font_family` | 0 | PARTIAL | `table.cell_set_text_font_family` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_formatting` | 0 | PARTIAL | `table.cell_set_text_formatting` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_halign` | 0 | PARTIAL | `table.cell_set_text_halign` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_size` | 0 | PARTIAL | `table.cell_set_text_size` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_text_valign` | 0 | PARTIAL | `table.cell_set_text_valign` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_tooltip` | 0 | PARTIAL | `table.cell_set_tooltip` · полный контракт не подтверждён · вызов через объект |
| `table.cell_set_width` | 0 | PARTIAL | `table.cell_set_width` · полный контракт не подтверждён · вызов через объект |
| `table.clear` | 0 | PARTIAL | `table.clear` · полный контракт не подтверждён · вызов через объект |
| `table.delete` | 0 | PARTIAL | `table.delete` · полный контракт не подтверждён · вызов через объект |
| `table.merge_cells` | 0 | PARTIAL | `table.merge_cells` · прямоугольники без пересечений, сохранение диапазона при очистке · вызов через объект |
| `table.new` | 0 | PARTIAL | `table.new_styled_overlay` · индикаторы и стратегии, девять позиций, 256 ячеек, именованные frame_color, frame_width, border_color, border_width |
| `table.set_bgcolor` | 0 | PARTIAL | `table.set_bgcolor` · полный контракт не подтверждён · вызов через объект |
| `table.set_border_color` | 0 | PARTIAL | `table.set_border_color` · полный контракт не подтверждён · вызов через объект |
| `table.set_border_width` | 0 | PARTIAL | `table.set_border_width` · полный контракт не подтверждён · вызов через объект |
| `table.set_frame_color` | 0 | PARTIAL | `table.set_frame_color` · полный контракт не подтверждён · вызов через объект |
| `table.set_frame_width` | 0 | PARTIAL | `table.set_frame_width` · полный контракт не подтверждён · вызов через объект |
| `table.set_position` | 0 | PARTIAL | `table.set_position` · полный контракт не подтверждён · вызов через объект |
| `ticker.heikinashi` | 0 | PARTIAL | фиксированный тикер, исторический security · [Профиль](HEIKIN_ASHI.md) |
| `ticker.heikinashi` | 1 | MISSING | динамический тикер · [Профиль](HEIKIN_ASHI.md) |
| `ticker.inherit` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `ticker.inherit` | 1 | MISSING | динамический тикер |
| `ticker.kagi` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.kagi` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.kagi` | 2 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.kagi` | 3 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.linebreak` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.linebreak` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.modify` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `ticker.modify` | 1 | MISSING | динамический тикер |
| `ticker.new` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `ticker.new` | 1 | MISSING | динамический тикер |
| `ticker.pointfigure` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.pointfigure` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.renko` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.renko` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `ticker.standard` | 0 | PARTIAL | фиксированный идентификатор рынка · [профиль](MARKET_TICKERS.md) |
| `ticker.standard` | 1 | MISSING | динамический тикер |
| `time` | 0 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `time` | 1 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `time_close` | 0 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `time_close` | 1 | PARTIAL | текущий бар и [старшие периоды UTC](HIGHER_TIMEFRAMES.md) · [Торговые сессии](TRADING_SESSIONS.md) · [Смещения времени](TIME_OFFSETS.md) |
| `timeframe.change` | 0 | PARTIAL | `environment.timeframe_change` · календарь UTC 24×7 · [Профиль](PIVOT_LEVELS.md) |
| `timeframe.from_seconds` | 0 | PARTIAL | `scalar.from_seconds` · полный контракт не подтверждён |
| `timeframe.from_seconds` | 1 | PARTIAL | `scalar.from_seconds` · полный контракт не подтверждён |
| `timeframe.in_seconds` | 0 | PARTIAL | `scalar.in_seconds` · полный контракт не подтверждён |
| `timeframe.in_seconds` | 1 | PARTIAL | `scalar.in_seconds` · полный контракт не подтверждён |
| `timestamp` | 0 | PARTIAL | константная дата и фиксированные смещения · [Профиль](DATE_INPUTS.md) |
| `timestamp` | 1 | MISSING | нет совпадающего допуска для перегрузки |
| `timestamp` | 2 | PARTIAL | `environment.timestamp6` · полный контракт не подтверждён |
| `timestamp` | 3 | PARTIAL | `environment.timestamp6` · полный контракт не подтверждён |
| `timestamp` | 4 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `timestamp` | 5 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `volume_row.buy_volume` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `volume_row.delta` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `volume_row.down_price` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `volume_row.has_buy_imbalance` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `volume_row.has_sell_imbalance` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `volume_row.sell_volume` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `volume_row.total_volume` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `volume_row.up_price` | 0 | MISSING | нет совпадающего допуска для перегрузки |
| `weekofyear` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |
| `year` | 0 | PARTIAL | закреплённые часовые пояса и календарные границы · [Профиль](CALENDAR_ZONES.md) |

## Недоступность по версиям

| Функция | N/A |
| --- | --- |
| `abs` | v5, v6 |
| `acos` | v5, v6 |
| `alert` | v1, v2, v3 |
| `alertcondition` | v1 |
| `alma` | v5, v6 |
| `array.abs` | v1, v2, v3, v4 |
| `array.avg` | v1, v2, v3 |
| `array.binary_search` | v1, v2, v3, v4 |
| `array.binary_search_leftmost` | v1, v2, v3, v4 |
| `array.binary_search_rightmost` | v1, v2, v3, v4 |
| `array.clear` | v1, v2, v3 |
| `array.concat` | v1, v2, v3 |
| `array.copy` | v1, v2, v3 |
| `array.covariance` | v1, v2, v3 |
| `array.every` | v1, v2, v3, v4 |
| `array.fill` | v1, v2, v3 |
| `array.first` | v1, v2, v3, v4 |
| `array.from` | v1, v2, v3 |
| `array.get` | v1, v2, v3 |
| `array.includes` | v1, v2, v3 |
| `array.indexof` | v1, v2, v3 |
| `array.insert` | v1, v2, v3 |
| `array.join` | v1, v2, v3 |
| `array.last` | v1, v2, v3, v4 |
| `array.lastindexof` | v1, v2, v3 |
| `array.max` | v1, v2, v3 |
| `array.median` | v1, v2, v3 |
| `array.min` | v1, v2, v3 |
| `array.mode` | v1, v2, v3 |
| `array.new<type>` | v1, v2, v3, v4 |
| `array.new_bool` | v1, v2, v3 |
| `array.new_box` | v1, v2, v3 |
| `array.new_color` | v1, v2, v3 |
| `array.new_float` | v1, v2, v3 |
| `array.new_int` | v1, v2, v3 |
| `array.new_label` | v1, v2, v3 |
| `array.new_line` | v1, v2, v3 |
| `array.new_linefill` | v1, v2, v3, v4 |
| `array.new_string` | v1, v2, v3 |
| `array.new_table` | v1, v2, v3 |
| `array.percentile_linear_interpolation` | v1, v2, v3, v4 |
| `array.percentile_nearest_rank` | v1, v2, v3, v4 |
| `array.percentrank` | v1, v2, v3, v4 |
| `array.pop` | v1, v2, v3 |
| `array.push` | v1, v2, v3 |
| `array.range` | v1, v2, v3 |
| `array.remove` | v1, v2, v3 |
| `array.reverse` | v1, v2, v3 |
| `array.set` | v1, v2, v3 |
| `array.shift` | v1, v2, v3 |
| `array.size` | v1, v2, v3 |
| `array.slice` | v1, v2, v3 |
| `array.some` | v1, v2, v3, v4 |
| `array.sort` | v1, v2, v3 |
| `array.sort_indices` | v1, v2, v3, v4 |
| `array.standardize` | v1, v2, v3 |
| `array.stdev` | v1, v2, v3 |
| `array.sum` | v1, v2, v3 |
| `array.unshift` | v1, v2, v3 |
| `array.variance` | v1, v2, v3 |
| `asin` | v5, v6 |
| `atan` | v5, v6 |
| `atr` | v5, v6 |
| `avg` | v5, v6 |
| `barssince` | v5, v6 |
| `bb` | v1, v2, v3, v5, v6 |
| `bbw` | v1, v2, v3, v5, v6 |
| `bool` | v1, v2, v3 |
| `box` | v1, v2, v3 |
| `box.copy` | v1, v2, v3, v4 |
| `box.delete` | v1, v2, v3 |
| `box.get_bottom` | v1, v2, v3 |
| `box.get_left` | v1, v2, v3 |
| `box.get_right` | v1, v2, v3 |
| `box.get_top` | v1, v2, v3 |
| `box.new` | v1, v2, v3 |
| `box.set_bgcolor` | v1, v2, v3 |
| `box.set_border_color` | v1, v2, v3 |
| `box.set_border_style` | v1, v2, v3 |
| `box.set_border_width` | v1, v2, v3 |
| `box.set_bottom` | v1, v2, v3 |
| `box.set_bottom_right_point` | v1, v2, v3, v4 |
| `box.set_extend` | v1, v2, v3 |
| `box.set_left` | v1, v2, v3 |
| `box.set_lefttop` | v1, v2, v3 |
| `box.set_right` | v1, v2, v3 |
| `box.set_rightbottom` | v1, v2, v3 |
| `box.set_text` | v1, v2, v3, v4 |
| `box.set_text_color` | v1, v2, v3, v4 |
| `box.set_text_font_family` | v1, v2, v3, v4 |
| `box.set_text_formatting` | v1, v2, v3, v4, v5 |
| `box.set_text_halign` | v1, v2, v3, v4 |
| `box.set_text_size` | v1, v2, v3, v4 |
| `box.set_text_valign` | v1, v2, v3, v4 |
| `box.set_text_wrap` | v1, v2, v3, v4 |
| `box.set_top` | v1, v2, v3 |
| `box.set_top_left_point` | v1, v2, v3, v4 |
| `box.set_xloc` | v1, v2, v3, v4, v5 |
| `cci` | v5, v6 |
| `ceil` | v5, v6 |
| `change` | v5, v6 |
| `chart.point.copy` | v1, v2, v3, v4 |
| `chart.point.from_index` | v1, v2, v3, v4 |
| `chart.point.from_time` | v1, v2, v3, v4 |
| `chart.point.new` | v1, v2, v3, v4 |
| `chart.point.now` | v1, v2, v3, v4 |
| `cmo` | v1, v2, v3, v5, v6 |
| `cog` | v5, v6 |
| `color` | v1 |
| `color.b` | v1, v2, v3 |
| `color.from_gradient` | v1, v2, v3 |
| `color.g` | v1, v2, v3 |
| `color.new` | v1, v2, v3 |
| `color.r` | v1, v2, v3 |
| `color.rgb` | v1, v2, v3 |
| `color.t` | v1, v2, v3 |
| `correlation` | v5, v6 |
| `cos` | v5, v6 |
| `cross` | v5, v6 |
| `crossover` | v5, v6 |
| `crossunder` | v5, v6 |
| `cum` | v5, v6 |
| `dev` | v5, v6 |
| `dividends` | v1, v2, v3, v5, v6 |
| `dmi` | v1, v2, v3, v5, v6 |
| `earnings` | v1, v2, v3, v5, v6 |
| `ema` | v5, v6 |
| `exp` | v5, v6 |
| `falling` | v5, v6 |
| `financial` | v1, v2, v3, v5, v6 |
| `float` | v1, v2, v3 |
| `floor` | v5, v6 |
| `footprint.buy_volume` | v1, v2, v3, v4, v5 |
| `footprint.delta` | v1, v2, v3, v4, v5 |
| `footprint.get_row_by_price` | v1, v2, v3, v4, v5 |
| `footprint.poc` | v1, v2, v3, v4, v5 |
| `footprint.rows` | v1, v2, v3, v4, v5 |
| `footprint.sell_volume` | v1, v2, v3, v4, v5 |
| `footprint.total_volume` | v1, v2, v3, v4, v5 |
| `footprint.vah` | v1, v2, v3, v4, v5 |
| `footprint.val` | v1, v2, v3, v4, v5 |
| `heikenashi` | v3, v4, v5, v6 |
| `heikinashi` | v1, v5, v6 |
| `highest` | v5, v6 |
| `highestbars` | v5, v6 |
| `hma` | v1, v2, v3, v5, v6 |
| `iff` | v5, v6 |
| `indicator` | v1, v2, v3, v4 |
| `input.bool` | v1, v2, v3, v4 |
| `input.color` | v1, v2, v3, v4 |
| `input.enum` | v1, v2, v3, v4 |
| `input.float` | v1, v2, v3, v4 |
| `input.int` | v1, v2, v3, v4 |
| `input.price` | v1, v2, v3, v4 |
| `input.session` | v1, v2, v3, v4 |
| `input.source` | v1, v2, v3, v4 |
| `input.string` | v1, v2, v3, v4 |
| `input.symbol` | v1, v2, v3, v4 |
| `input.text_area` | v1, v2, v3, v4 |
| `input.time` | v1, v2, v3, v4 |
| `input.timeframe` | v1, v2, v3, v4 |
| `int` | v1, v2, v3 |
| `kagi` | v5, v6 |
| `kc` | v1, v2, v3, v5, v6 |
| `kcw` | v1, v2, v3, v5, v6 |
| `label` | v1, v2, v3 |
| `label.copy` | v1, v2, v3, v4 |
| `label.delete` | v1, v2, v3 |
| `label.get_text` | v1, v2, v3 |
| `label.get_x` | v1, v2, v3 |
| `label.get_y` | v1, v2, v3 |
| `label.new` | v1, v2, v3 |
| `label.set_color` | v1, v2, v3 |
| `label.set_point` | v1, v2, v3, v4 |
| `label.set_size` | v1, v2, v3 |
| `label.set_style` | v1, v2, v3 |
| `label.set_text` | v1, v2, v3 |
| `label.set_text_font_family` | v1, v2, v3, v4 |
| `label.set_text_formatting` | v1, v2, v3, v4, v5 |
| `label.set_textalign` | v1, v2, v3 |
| `label.set_textcolor` | v1, v2, v3 |
| `label.set_tooltip` | v1, v2, v3 |
| `label.set_x` | v1, v2, v3 |
| `label.set_xloc` | v1, v2, v3 |
| `label.set_xy` | v1, v2, v3 |
| `label.set_y` | v1, v2, v3 |
| `label.set_yloc` | v1, v2, v3 |
| `library` | v1, v2, v3, v4 |
| `line` | v1, v2, v3 |
| `line.copy` | v1, v2, v3, v4 |
| `line.delete` | v1, v2, v3 |
| `line.get_price` | v1, v2, v3 |
| `line.get_x1` | v1, v2, v3 |
| `line.get_x2` | v1, v2, v3 |
| `line.get_y1` | v1, v2, v3 |
| `line.get_y2` | v1, v2, v3 |
| `line.new` | v1, v2, v3 |
| `line.set_color` | v1, v2, v3 |
| `line.set_extend` | v1, v2, v3 |
| `line.set_first_point` | v1, v2, v3, v4 |
| `line.set_second_point` | v1, v2, v3, v4 |
| `line.set_style` | v1, v2, v3 |
| `line.set_width` | v1, v2, v3 |
| `line.set_x1` | v1, v2, v3 |
| `line.set_x2` | v1, v2, v3 |
| `line.set_xloc` | v1, v2, v3 |
| `line.set_xy1` | v1, v2, v3 |
| `line.set_xy2` | v1, v2, v3 |
| `line.set_y1` | v1, v2, v3 |
| `line.set_y2` | v1, v2, v3 |
| `linebreak` | v5, v6 |
| `linefill` | v1, v2, v3, v4 |
| `linefill.delete` | v1, v2, v3, v4 |
| `linefill.get_line1` | v1, v2, v3, v4 |
| `linefill.get_line2` | v1, v2, v3, v4 |
| `linefill.new` | v1, v2, v3, v4 |
| `linefill.set_color` | v1, v2, v3, v4 |
| `linreg` | v5, v6 |
| `log` | v5, v6 |
| `log.error` | v1, v2, v3, v4 |
| `log.info` | v1, v2, v3, v4 |
| `log.warning` | v1, v2, v3, v4 |
| `log10` | v5, v6 |
| `lowest` | v5, v6 |
| `lowestbars` | v5, v6 |
| `macd` | v5, v6 |
| `map.clear` | v1, v2, v3, v4 |
| `map.contains` | v1, v2, v3, v4 |
| `map.copy` | v1, v2, v3, v4 |
| `map.get` | v1, v2, v3, v4 |
| `map.keys` | v1, v2, v3, v4 |
| `map.new<type,type>` | v1, v2, v3, v4 |
| `map.put` | v1, v2, v3, v4 |
| `map.put_all` | v1, v2, v3, v4 |
| `map.remove` | v1, v2, v3, v4 |
| `map.size` | v1, v2, v3, v4 |
| `map.values` | v1, v2, v3, v4 |
| `math.abs` | v1, v2, v3, v4 |
| `math.acos` | v1, v2, v3, v4 |
| `math.asin` | v1, v2, v3, v4 |
| `math.atan` | v1, v2, v3, v4 |
| `math.avg` | v1, v2, v3, v4 |
| `math.ceil` | v1, v2, v3, v4 |
| `math.cos` | v1, v2, v3, v4 |
| `math.exp` | v1, v2, v3, v4 |
| `math.floor` | v1, v2, v3, v4 |
| `math.log` | v1, v2, v3, v4 |
| `math.log10` | v1, v2, v3, v4 |
| `math.max` | v1, v2, v3, v4 |
| `math.min` | v1, v2, v3, v4 |
| `math.pow` | v1, v2, v3, v4 |
| `math.random` | v1, v2, v3, v4 |
| `math.round` | v1, v2, v3, v4 |
| `math.round_to_mintick` | v1, v2, v3, v4 |
| `math.sign` | v1, v2, v3, v4 |
| `math.sin` | v1, v2, v3, v4 |
| `math.sqrt` | v1, v2, v3, v4 |
| `math.sum` | v1, v2, v3, v4 |
| `math.tan` | v1, v2, v3, v4 |
| `math.todegrees` | v1, v2, v3, v4 |
| `math.toradians` | v1, v2, v3, v4 |
| `matrix.add_col` | v1, v2, v3, v4 |
| `matrix.add_row` | v1, v2, v3, v4 |
| `matrix.avg` | v1, v2, v3, v4 |
| `matrix.col` | v1, v2, v3, v4 |
| `matrix.columns` | v1, v2, v3, v4 |
| `matrix.concat` | v1, v2, v3, v4 |
| `matrix.copy` | v1, v2, v3, v4 |
| `matrix.det` | v1, v2, v3, v4 |
| `matrix.diff` | v1, v2, v3, v4 |
| `matrix.eigenvalues` | v1, v2, v3, v4 |
| `matrix.eigenvectors` | v1, v2, v3, v4 |
| `matrix.elements_count` | v1, v2, v3, v4 |
| `matrix.fill` | v1, v2, v3, v4 |
| `matrix.get` | v1, v2, v3, v4 |
| `matrix.inv` | v1, v2, v3, v4 |
| `matrix.is_antidiagonal` | v1, v2, v3, v4 |
| `matrix.is_antisymmetric` | v1, v2, v3, v4 |
| `matrix.is_binary` | v1, v2, v3, v4 |
| `matrix.is_diagonal` | v1, v2, v3, v4 |
| `matrix.is_identity` | v1, v2, v3, v4 |
| `matrix.is_square` | v1, v2, v3, v4 |
| `matrix.is_stochastic` | v1, v2, v3, v4 |
| `matrix.is_symmetric` | v1, v2, v3, v4 |
| `matrix.is_triangular` | v1, v2, v3, v4 |
| `matrix.is_zero` | v1, v2, v3, v4 |
| `matrix.kron` | v1, v2, v3, v4 |
| `matrix.max` | v1, v2, v3, v4 |
| `matrix.median` | v1, v2, v3, v4 |
| `matrix.min` | v1, v2, v3, v4 |
| `matrix.mode` | v1, v2, v3, v4 |
| `matrix.mult` | v1, v2, v3, v4 |
| `matrix.new<type>` | v1, v2, v3, v4 |
| `matrix.pinv` | v1, v2, v3, v4 |
| `matrix.pow` | v1, v2, v3, v4 |
| `matrix.rank` | v1, v2, v3, v4 |
| `matrix.remove_col` | v1, v2, v3, v4 |
| `matrix.remove_row` | v1, v2, v3, v4 |
| `matrix.reshape` | v1, v2, v3, v4 |
| `matrix.reverse` | v1, v2, v3, v4 |
| `matrix.row` | v1, v2, v3, v4 |
| `matrix.rows` | v1, v2, v3, v4 |
| `matrix.set` | v1, v2, v3, v4 |
| `matrix.sort` | v1, v2, v3, v4 |
| `matrix.submatrix` | v1, v2, v3, v4 |
| `matrix.sum` | v1, v2, v3, v4 |
| `matrix.swap_columns` | v1, v2, v3, v4 |
| `matrix.swap_rows` | v1, v2, v3, v4 |
| `matrix.trace` | v1, v2, v3, v4 |
| `matrix.transpose` | v1, v2, v3, v4 |
| `max` | v5, v6 |
| `max_bars_back` | v1, v2, v3 |
| `median` | v1, v2, v3, v5, v6 |
| `mfi` | v1, v3, v5, v6 |
| `min` | v5, v6 |
| `mode` | v1, v2, v3, v5, v6 |
| `mom` | v5, v6 |
| `offset` | v5, v6 |
| `percentile_linear_interpolation` | v5, v6 |
| `percentile_nearest_rank` | v5, v6 |
| `percentrank` | v5, v6 |
| `pivothigh` | v5, v6 |
| `pivotlow` | v5, v6 |
| `pointfigure` | v5, v6 |
| `polyline.delete` | v1, v2, v3, v4 |
| `polyline.new` | v1, v2, v3, v4 |
| `pow` | v5, v6 |
| `quandl` | v1, v2, v3, v5, v6 |
| `random` | v1, v2, v3, v5, v6 |
| `range` | v1, v2, v3, v5, v6 |
| `renko` | v5, v6 |
| `request.currency_rate` | v1, v2, v3, v4 |
| `request.dividends` | v1, v2, v3, v4 |
| `request.earnings` | v1, v2, v3, v4 |
| `request.economic` | v1, v2, v3, v4 |
| `request.financial` | v1, v2, v3, v4 |
| `request.footprint` | v1, v2, v3, v4, v5 |
| `request.quandl` | v1, v2, v3, v4 |
| `request.security` | v1, v2, v3, v4 |
| `request.security_lower_tf` | v1, v2, v3, v4 |
| `request.seed` | v1, v2, v3, v4 |
| `request.splits` | v1, v2, v3, v4 |
| `rising` | v5, v6 |
| `rma` | v5, v6 |
| `roc` | v5, v6 |
| `round` | v5, v6 |
| `round_to_mintick` | v1, v2, v3, v5, v6 |
| `rsi` | v5, v6 |
| `runtime.error` | v1, v2, v3, v4 |
| `sar` | v5, v6 |
| `security` | v5, v6 |
| `sign` | v5, v6 |
| `sin` | v5, v6 |
| `sma` | v5, v6 |
| `splits` | v1, v2, v3, v5, v6 |
| `sqrt` | v5, v6 |
| `stdev` | v5, v6 |
| `stoch` | v5, v6 |
| `str.contains` | v1, v2, v3, v4 |
| `str.endswith` | v1, v2, v3, v4 |
| `str.format` | v1, v2, v3 |
| `str.format_time` | v1, v2, v3, v4 |
| `str.length` | v1, v2, v3 |
| `str.lower` | v1, v2, v3, v4 |
| `str.match` | v1, v2, v3, v4 |
| `str.pos` | v1, v2, v3, v4 |
| `str.repeat` | v1, v2, v3, v4 |
| `str.replace` | v1, v2, v3, v4 |
| `str.replace_all` | v1, v2, v3 |
| `str.split` | v1, v2, v3 |
| `str.startswith` | v1, v2, v3, v4 |
| `str.substring` | v1, v2, v3, v4 |
| `str.tonumber` | v1, v2, v3, v4 |
| `str.tostring` | v1, v2, v3, v4 |
| `str.trim` | v1, v2, v3, v4 |
| `str.upper` | v1, v2, v3, v4 |
| `strategy.closedtrades.commission` | v1, v2, v3, v4 |
| `strategy.closedtrades.entry_bar_index` | v1, v2, v3, v4 |
| `strategy.closedtrades.entry_comment` | v1, v2, v3, v4 |
| `strategy.closedtrades.entry_id` | v1, v2, v3, v4 |
| `strategy.closedtrades.entry_price` | v1, v2, v3, v4 |
| `strategy.closedtrades.entry_time` | v1, v2, v3, v4 |
| `strategy.closedtrades.exit_bar_index` | v1, v2, v3, v4 |
| `strategy.closedtrades.exit_comment` | v1, v2, v3, v4 |
| `strategy.closedtrades.exit_id` | v1, v2, v3, v4 |
| `strategy.closedtrades.exit_price` | v1, v2, v3, v4 |
| `strategy.closedtrades.exit_time` | v1, v2, v3, v4 |
| `strategy.closedtrades.max_drawdown` | v1, v2, v3, v4 |
| `strategy.closedtrades.max_drawdown_percent` | v1, v2, v3, v4 |
| `strategy.closedtrades.max_runup` | v1, v2, v3, v4 |
| `strategy.closedtrades.max_runup_percent` | v1, v2, v3, v4 |
| `strategy.closedtrades.profit` | v1, v2, v3, v4 |
| `strategy.closedtrades.profit_percent` | v1, v2, v3, v4 |
| `strategy.closedtrades.size` | v1, v2, v3, v4 |
| `strategy.convert_to_account` | v1, v2, v3, v4 |
| `strategy.convert_to_symbol` | v1, v2, v3, v4 |
| `strategy.default_entry_qty` | v1, v2, v3, v4 |
| `strategy.opentrades.commission` | v1, v2, v3, v4 |
| `strategy.opentrades.entry_bar_index` | v1, v2, v3, v4 |
| `strategy.opentrades.entry_comment` | v1, v2, v3, v4 |
| `strategy.opentrades.entry_id` | v1, v2, v3, v4 |
| `strategy.opentrades.entry_price` | v1, v2, v3, v4 |
| `strategy.opentrades.entry_time` | v1, v2, v3, v4 |
| `strategy.opentrades.max_drawdown` | v1, v2, v3, v4 |
| `strategy.opentrades.max_drawdown_percent` | v1, v2, v3, v4 |
| `strategy.opentrades.max_runup` | v1, v2, v3, v4 |
| `strategy.opentrades.max_runup_percent` | v1, v2, v3, v4 |
| `strategy.opentrades.profit` | v1, v2, v3, v4 |
| `strategy.opentrades.profit_percent` | v1, v2, v3, v4 |
| `strategy.opentrades.size` | v1, v2, v3, v4 |
| `string` | v1, v2, v3 |
| `study` | v5, v6 |
| `sum` | v5, v6 |
| `supertrend` | v1, v2, v3, v5, v6 |
| `swma` | v5, v6 |
| `syminfo.prefix` | v1, v2, v3, v4 |
| `syminfo.ticker` | v1, v2, v3, v4 |
| `ta.alma` | v1, v2, v3, v4 |
| `ta.atr` | v1, v2, v3, v4 |
| `ta.barssince` | v1, v2, v3, v4 |
| `ta.bb` | v1, v2, v3, v4 |
| `ta.bbw` | v1, v2, v3, v4 |
| `ta.cci` | v1, v2, v3, v4 |
| `ta.change` | v1, v2, v3, v4 |
| `ta.cmo` | v1, v2, v3, v4 |
| `ta.cog` | v1, v2, v3, v4 |
| `ta.correlation` | v1, v2, v3, v4 |
| `ta.cross` | v1, v2, v3, v4 |
| `ta.crossover` | v1, v2, v3, v4 |
| `ta.crossunder` | v1, v2, v3, v4 |
| `ta.cum` | v1, v2, v3, v4 |
| `ta.dev` | v1, v2, v3, v4 |
| `ta.dmi` | v1, v2, v3, v4 |
| `ta.ema` | v1, v2, v3, v4 |
| `ta.falling` | v1, v2, v3, v4 |
| `ta.highest` | v1, v2, v3, v4 |
| `ta.highestbars` | v1, v2, v3, v4 |
| `ta.hma` | v1, v2, v3, v4 |
| `ta.kc` | v1, v2, v3, v4 |
| `ta.kcw` | v1, v2, v3, v4 |
| `ta.linreg` | v1, v2, v3, v4 |
| `ta.lowest` | v1, v2, v3, v4 |
| `ta.lowestbars` | v1, v2, v3, v4 |
| `ta.macd` | v1, v2, v3, v4 |
| `ta.max` | v1, v2, v3, v4 |
| `ta.median` | v1, v2, v3, v4 |
| `ta.mfi` | v1, v2, v3, v4 |
| `ta.min` | v1, v2, v3, v4 |
| `ta.mode` | v1, v2, v3, v4 |
| `ta.mom` | v1, v2, v3, v4 |
| `ta.percentile_linear_interpolation` | v1, v2, v3, v4 |
| `ta.percentile_nearest_rank` | v1, v2, v3, v4 |
| `ta.percentrank` | v1, v2, v3, v4 |
| `ta.pivot_point_levels` | v1, v2, v3, v4 |
| `ta.pivothigh` | v1, v2, v3, v4 |
| `ta.pivotlow` | v1, v2, v3, v4 |
| `ta.range` | v1, v2, v3, v4 |
| `ta.rci` | v1, v2, v3, v4, v5 |
| `ta.rising` | v1, v2, v3, v4 |
| `ta.rma` | v1, v2, v3, v4 |
| `ta.roc` | v1, v2, v3, v4 |
| `ta.rsi` | v1, v2, v3, v4 |
| `ta.sar` | v1, v2, v3, v4 |
| `ta.sma` | v1, v2, v3, v4 |
| `ta.stdev` | v1, v2, v3, v4 |
| `ta.stoch` | v1, v2, v3, v4 |
| `ta.supertrend` | v1, v2, v3, v4 |
| `ta.swma` | v1, v2, v3, v4 |
| `ta.tr` | v1, v2, v3, v4 |
| `ta.tsi` | v1, v2, v3, v4 |
| `ta.valuewhen` | v1, v2, v3, v4 |
| `ta.variance` | v1, v2, v3, v4 |
| `ta.vwap` | v1, v2, v3, v4 |
| `ta.vwma` | v1, v2, v3, v4 |
| `ta.wma` | v1, v2, v3, v4 |
| `ta.wpr` | v1, v2, v3, v4 |
| `table` | v1, v2, v3 |
| `table.cell` | v1, v2, v3 |
| `table.cell_set_bgcolor` | v1, v2, v3 |
| `table.cell_set_height` | v1, v2, v3 |
| `table.cell_set_text` | v1, v2, v3 |
| `table.cell_set_text_color` | v1, v2, v3 |
| `table.cell_set_text_font_family` | v1, v2, v3, v4 |
| `table.cell_set_text_formatting` | v1, v2, v3, v4, v5 |
| `table.cell_set_text_halign` | v1, v2, v3 |
| `table.cell_set_text_size` | v1, v2, v3 |
| `table.cell_set_text_valign` | v1, v2, v3 |
| `table.cell_set_tooltip` | v1, v2, v3, v4 |
| `table.cell_set_width` | v1, v2, v3 |
| `table.clear` | v1, v2, v3 |
| `table.delete` | v1, v2, v3 |
| `table.merge_cells` | v1, v2, v3, v4 |
| `table.new` | v1, v2, v3 |
| `table.set_bgcolor` | v1, v2, v3 |
| `table.set_border_color` | v1, v2, v3 |
| `table.set_border_width` | v1, v2, v3 |
| `table.set_frame_color` | v1, v2, v3 |
| `table.set_frame_width` | v1, v2, v3 |
| `table.set_position` | v1, v2, v3 |
| `tan` | v5, v6 |
| `ticker.heikinashi` | v1, v2, v3, v4 |
| `ticker.inherit` | v1, v2, v3, v4 |
| `ticker.kagi` | v1, v2, v3, v4 |
| `ticker.linebreak` | v1, v2, v3, v4 |
| `ticker.modify` | v1, v2, v3, v4 |
| `ticker.new` | v1, v2, v3, v4 |
| `ticker.pointfigure` | v1, v2, v3, v4 |
| `ticker.renko` | v1, v2, v3, v4 |
| `ticker.standard` | v1, v2, v3, v4 |
| `tickerid` | v5, v6 |
| `time_close` | v1, v2, v3 |
| `timeframe.change` | v1, v2, v3, v4 |
| `timeframe.from_seconds` | v1, v2, v3, v4 |
| `timeframe.in_seconds` | v1, v2, v3, v4 |
| `timestamp` | v1 |
| `todegrees` | v1, v2, v3, v5, v6 |
| `tonumber` | v1, v2, v3, v5, v6 |
| `toradians` | v1, v2, v3, v5, v6 |
| `tostring` | v5, v6 |
| `tr` | v5, v6 |
| `tsi` | v5, v6 |
| `valuewhen` | v5, v6 |
| `variance` | v5, v6 |
| `volume_row.buy_volume` | v1, v2, v3, v4, v5 |
| `volume_row.delta` | v1, v2, v3, v4, v5 |
| `volume_row.down_price` | v1, v2, v3, v4, v5 |
| `volume_row.has_buy_imbalance` | v1, v2, v3, v4, v5 |
| `volume_row.has_sell_imbalance` | v1, v2, v3, v4, v5 |
| `volume_row.sell_volume` | v1, v2, v3, v4, v5 |
| `volume_row.total_volume` | v1, v2, v3, v4, v5 |
| `volume_row.up_price` | v1, v2, v3, v4, v5 |
| `vwap` | v5, v6 |
| `vwma` | v5, v6 |
| `wma` | v5, v6 |
| `wpr` | v1, v3, v5, v6 |

## Дополнительные сигнатуры

В v5–v6 также допущен вертикальный градиент между двумя `hline`.
Эта сигнатура подтверждена материалами TradingView и учитывается отдельно от строк закреплённого справочника.
[ADR 0032](adr/0032-gradient-fill.md)

## Переменные графических объектов

| Переменная | v1–v4 | v5–v6 | Граница |
| --- | --- | --- | --- |
| `box.all` | N/A | PARTIAL | [Снимок живых ID, чтение и перебор](DRAWING_OBJECTS.md) |
| `label.all` | N/A | PARTIAL | [Снимок живых ID, чтение и перебор](DRAWING_OBJECTS.md) |
| `line.all` | N/A | PARTIAL | [Снимок живых ID, чтение и перебор](DRAWING_OBJECTS.md) |
| `linefill.all` | N/A | PARTIAL | [Снимок живых ID, чтение и перебор](DRAWING_OBJECTS.md) |
| `polyline.all` | N/A | PARTIAL | [Снимок живых ID, чтение и перебор](DRAWING_OBJECTS.md) |
| `table.all` | N/A | PARTIAL | [Снимок живых ID, чтение и перебор](DRAWING_OBJECTS.md) |

## Встроенные переменные объёма

| Переменная | Pine | Статус | Граница |
| --- | --- | --- | --- |
| `accdist` | v1 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `vwap` | v1 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `accdist` | v2 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `vwap` | v2 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `accdist` | v3 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `vwap` | v3 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `accdist` | v4 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `vwap` | v4 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `obv` | v4 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `pvt` | v4 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `ta.accdist` | v5 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `ta.vwap` | v5 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `ta.obv` | v5 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `ta.pvt` | v5 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `ta.accdist` | v6 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `ta.vwap` | v6 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `ta.obv` | v6 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |
| `ta.pvt` | v6 | PARTIAL | [Профиль](VOLUME_INDICATORS.md) |

## Встроенные переменные границ графика

| Переменная | Pine | Статус | Граница |
| --- | --- | --- | --- |
| `last_bar_index` | v5–v6 | PARTIAL | [Профиль](CHART_CLOCK.md) |
| `last_bar_index` | v1–v4 | N/A | — |
| `last_bar_time` | v5–v6 | PARTIAL | [Профиль](CHART_CLOCK.md) |
| `last_bar_time` | v1–v4 | N/A | — |
| `time_tradingday` | v4–v6 | PARTIAL | [Профиль](CHART_CLOCK.md) |
| `time_tradingday` | v1–v3 | N/A | — |

## Флаги торговой сессии инструмента

| Переменная | Pine | Статус | Граница |
| --- | --- | --- | --- |
| `session.isfirstbar` | v5–v6 | PARTIAL | [Профиль UTC 24×7](SESSION_FLAGS.md) |
| `session.isfirstbar` | v1–v4 | N/A | — |
| `session.isfirstbar_regular` | v5–v6 | PARTIAL | [Профиль UTC 24×7](SESSION_FLAGS.md) |
| `session.isfirstbar_regular` | v1–v4 | N/A | — |
| `session.islastbar` | v5–v6 | PARTIAL | [Профиль UTC 24×7](SESSION_FLAGS.md) |
| `session.islastbar` | v1–v4 | N/A | — |
| `session.islastbar_regular` | v5–v6 | PARTIAL | [Профиль UTC 24×7](SESSION_FLAGS.md) |
| `session.islastbar_regular` | v1–v4 | N/A | — |
| `session.ismarket` | v4–v6 | PARTIAL | [Профиль UTC 24×7](SESSION_FLAGS.md) |
| `session.ismarket` | v1–v3 | N/A | — |
| `session.ispostmarket` | v4–v6 | PARTIAL | [Профиль UTC 24×7](SESSION_FLAGS.md) |
| `session.ispostmarket` | v1–v3 | N/A | — |
| `session.ispremarket` | v4–v6 | PARTIAL | [Профиль UTC 24×7](SESSION_FLAGS.md) |
| `session.ispremarket` | v1–v3 | N/A | — |

## Время исполнения

| Переменная | Pine | Статус | Граница |
| --- | --- | --- | --- |
| `timenow` | v3–v6 | PARTIAL | [Закреплённые часы истории и события replay](EXECUTION_TIME.md) |
| `timenow` | v1–v2 | N/A | — |

## Видимый диапазон графика

| Переменная | Pine | Статус | Граница |
| --- | --- | --- | --- |
| `chart.left_visible_bar_time` | v5–v6 | PARTIAL | [Исторический снимок](VISIBLE_RANGE.md) |
| `chart.left_visible_bar_time` | v1–v4 | N/A | — |
| `chart.right_visible_bar_time` | v5–v6 | PARTIAL | [Исторический снимок](VISIBLE_RANGE.md) |
| `chart.right_visible_bar_time` | v1–v4 | N/A | — |

## Инвентарь сценариев

Здесь указаны ожидаемые исходы по файлам сценариев.

| Профиль | Исполнения | Ожидаемые отказы |
| --- | ---: | ---: |
| [alerts](../conformance/alerts/cases.json) | 13 | 17 |
| [array-operations](../conformance/array-operations/cases.json) | 75 | 64 |
| [array-order](../conformance/array-order/cases.json) | 68 | 70 |
| [array-slice](../conformance/array-slice/cases.json) | 72 | 39 |
| [arrays](../conformance/arrays/cases.json) | 46 | 54 |
| [box-borders](../conformance/box-borders/cases.json) | 9 | 21 |
| [box-text](../conformance/box-text/cases.json) | 9 | 30 |
| [boxes](../conformance/boxes/cases.json) | 9 | 18 |
| [calendar-zones](../conformance/calendar-zones/cases.json) | 17 | 10 |
| [change-valuewhen](../conformance/change-valuewhen/cases.json) | 22 | 6 |
| [channel-indicators](../conformance/channel-indicators/cases.json) | 12 | 18 |
| [chart-clock](../conformance/chart-clock/cases.json) | 14 | 11 |
| [chart-points](../conformance/chart-points/cases.json) | 6 | 16 |
| [colors](../conformance/colors/cases.json) | 74 | 39 |
| [composite](../conformance/composite/cases.json) | 42 | 30 |
| [computed-targets](../conformance/computed-targets/cases.json) | 16 | 6 |
| [context-graph](../conformance/context-graph/cases.json) | 60 | 30 |
| [contexts](../conformance/contexts/cases.json) | 38 | 0 |
| [corporate-actions](../conformance/corporate-actions/cases.json) | 7 | 6 |
| [correlation](../conformance/correlation/cases.json) | 10 | 15 |
| [currency-rates](../conformance/currency-rates/cases.json) | 9 | 8 |
| [date-inputs](../conformance/date-inputs/cases.json) | 6 | 18 |
| [drawing-xloc](../conformance/drawing-xloc/cases.json) | 8 | 15 |
| [drawing-xloc-mutations](../conformance/drawing-xloc-mutations/cases.json) | 9 | 26 |
| [dynamic-signals](../conformance/dynamic-signals/cases.json) | 18 | 30 |
| [dynamic-windows](../conformance/dynamic-windows/cases.json) | 26 | 36 |
| [earnings](../conformance/earnings/cases.json) | 10 | 14 |
| [economic-data](../conformance/economic-data/cases.json) | 8 | 18 |
| [economics](../conformance/economics/cases.json) | 78 | 43 |
| [enums](../conformance/enums/cases.json) | 45 | 39 |
| [environment](../conformance/environment/cases.json) | 63 | 18 |
| [execution-time](../conformance/execution-time/cases.json) | 4 | 6 |
| [exits](../conformance/exits/cases.json) | 18 | 6 |
| [extrema](../conformance/extrema/cases.json) | 20 | 10 |
| [financial](../conformance/financial/cases.json) | 49 | 33 |
| [function-overloads](../conformance/function-overloads/cases.json) | 20 | 20 |
| [function-targets](../conformance/function-targets/cases.json) | 32 | 6 |
| [functions](../conformance/functions/cases.json) | 60 | 48 |
| [heikin-ashi](../conformance/heikin-ashi/cases.json) | 10 | 1 |
| [higher-timeframes](../conformance/higher-timeframes/cases.json) | 18 | 6 |
| [historical-bars](../conformance/historical-bars/cases.json) | 6 | 5 |
| [inputs](../conformance/inputs/cases.json) | 24 | 0 |
| [label-styles](../conformance/label-styles/cases.json) | 13 | 27 |
| [label-text](../conformance/label-text/cases.json) | 9 | 36 |
| [label-yloc](../conformance/label-yloc/cases.json) | 6 | 19 |
| [labels](../conformance/labels/cases.json) | 9 | 17 |
| [language](../conformance/language/cases.json) | 50 | 34 |
| [line-extensions](../conformance/line-extensions/cases.json) | 6 | 9 |
| [line-styles](../conformance/line-styles/cases.json) | 13 | 29 |
| [linefills](../conformance/linefills/cases.json) | 20 | 37 |
| [lines](../conformance/lines/cases.json) | 9 | 17 |
| [literal-harness](../conformance/literal-harness/cases.json) | 5 | 3 |
| [loops](../conformance/loops/cases.json) | 60 | 32 |
| [lots](../conformance/lots/cases.json) | 120 | 30 |
| [lower-tf](../conformance/lower-tf/cases.json) | 12 | 10 |
| [maps](../conformance/maps/cases.json) | 60 | 41 |
| [market-tickers](../conformance/market-tickers/cases.json) | 7 | 5 |
| [matrices](../conformance/matrices/cases.json) | 74 | 40 |
| [methods](../conformance/methods/cases.json) | 20 | 24 |
| [momentum](../conformance/momentum/cases.json) | 15 | 15 |
| [numeric-aggregates](../conformance/numeric-aggregates/cases.json) | 15 | 6 |
| [numeric-targets](../conformance/numeric-targets/cases.json) | 36 | 22 |
| [object-arrays](../conformance/object-arrays/cases.json) | 42 | 26 |
| [object-graphs](../conformance/object-graphs/cases.json) | 48 | 46 |
| [objects](../conformance/objects/cases.json) | 36 | 30 |
| [optional-lower-symbols](../conformance/optional-lower-symbols/cases.json) | 6 | 14 |
| [optional-symbols](../conformance/optional-symbols/cases.json) | 11 | 16 |
| [order-groups](../conformance/order-groups/cases.json) | 132 | 54 |
| [partial-exits](../conformance/partial-exits/cases.json) | 60 | 24 |
| [pending-entries](../conformance/pending-entries/cases.json) | 90 | 30 |
| [performance](../conformance/performance/cases.json) | 36 | 14 |
| [pivot-levels](../conformance/pivot-levels/cases.json) | 6 | 14 |
| [pivots](../conformance/pivots/cases.json) | 12 | 10 |
| [polylines](../conformance/polylines/cases.json) | 14 | 41 |
| [prepared-requests](../conformance/prepared-requests/cases.json) | 14 | 6 |
| [primary-targets](../conformance/primary-targets/cases.json) | 32 | 6 |
| [product-results](../conformance/product-results/cases.json) | 40 | 12 |
| [request-environment](../conformance/request-environment/cases.json) | 27 | 15 |
| [risk](../conformance/risk/cases.json) | 36 | 60 |
| [scalar-targets](../conformance/scalar-targets/cases.json) | 35 | 12 |
| [security-count](../conformance/security-count/cases.json) | 8 | 12 |
| [security-currency](../conformance/security-currency/cases.json) | 8 | 6 |
| [semantic-core](../conformance/semantic-core/cases.json) | 12 | 10 |
| [series](../conformance/series/cases.json) | 54 | 42 |
| [session-flags](../conformance/session-flags/cases.json) | 19 | 31 |
| [signals](../conformance/signals/cases.json) | 108 | 20 |
| [strategies](../conformance/strategies/cases.json) | 24 | 0 |
| [string-format](../conformance/string-format/cases.json) | 9 | 18 |
| [tables](../conformance/tables/cases.json) | 31 | 71 |
| [technical](../conformance/technical/cases.json) | 54 | 18 |
| [tick-price](../conformance/tick-price/cases.json) | 12 | 12 |
| [time-format](../conformance/time-format/cases.json) | 10 | 18 |
| [time-offsets](../conformance/time-offsets/cases.json) | 5 | 9 |
| [trading-sessions](../conformance/trading-sessions/cases.json) | 30 | 15 |
| [trailing](../conformance/trailing/cases.json) | 109 | 53 |
| [trend-indicators](../conformance/trend-indicators/cases.json) | 12 | 9 |
| [trigonometry](../conformance/trigonometry/cases.json) | 22 | 19 |
| [versions](../conformance/versions/cases.json) | 36 | 26 |
| [visible-range](../conformance/visible-range/cases.json) | 8 | 10 |
| [visual-bands](../conformance/visual-bands/cases.json) | 64 | 90 |
| [visual-ohlc](../conformance/visual-ohlc/cases.json) | 23 | 28 |
| [visual-signals](../conformance/visual-signals/cases.json) | 44 | 73 |
| [volume-indicators](../conformance/volume-indicators/cases.json) | 16 | 8 |
| [weighted-indicators](../conformance/weighted-indicators/cases.json) | 19 | 17 |
| [window-indicators](../conformance/window-indicators/cases.json) | 9 | 9 |
| [window-statistics](../conformance/window-statistics/cases.json) | 9 | 15 |
