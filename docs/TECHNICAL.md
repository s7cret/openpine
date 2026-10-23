# Практические индикаторы и стратегии v1–v6

Профили `openpine.technical.v1.1` … `v6.1` закреплены выпуском
`pine-technical-20261023.1`: RMA, True Range, ATR, RSI, highest и lowest.
Имена/сигнатуры сверены с локальными версионными артефактами. Для неполных
v1/v2 и неуточнённых числовых случаев приняты явные правила нашей модели.
Независимые ожидания проверяют эти правила; исполнение в TradingView пока
не использовалось как эталон.

## API и числовые правила

| Операция | v1–v4 | v5–v6 | Поведение принятого профиля |
| --- | --- | --- | --- |
| RMA | `rma(source,length)` | `ta.rma(source,length)` | Среднее первых `length` определённых значений; затем `alpha=1/length`; `na` не обновляет состояние |
| True Range | `tr`, `tr(handle_na)` | `ta.tr`, `ta.tr(handle_na)` | Максимум `high-low`, `abs(high-close[1])`, `abs(low-close[1])`; предыдущий close относится к предыдущему физическому бару |
| ATR | `atr(length)` | `ta.atr(length)` | RMA от TR с `handle_na=true` |
| RSI | `rsi(source,length)` | `ta.rsi(source,length)` | Два RMA роста/падения; прогрев по `length` изменениям между определёнными значениями |
| Максимум | `highest(source,length)`, `highest(length)` | `ta.highest(source,length)`, `ta.highest(length)` | Последние `length` достигнутых вызовов, включая пропуски; сокращённая форма использует `high` |
| Минимум | `lowest(source,length)`, `lowest(length)` | `ta.lowest(source,length)`, `ta.lowest(length)` | То же окно; сокращённая форма использует `low` |

В v1/v2 аргументы новых TA-функций позиционные. Длина — `simple int`, включая
`input.int`/старый `input`, от 1 до 100000. Меняющаяся по барам длина не входит
в этот профиль. Параметры можно изменять между независимыми заданиями.
У RSI поддержана только перегрузка `source, simple-int length`; историческая
перегрузка отношения двух рядов не входит в профиль.

У переменной `tr` поведение `handle_na=false`: без предыдущего close результат
`na`. Форма `tr(true)` при отсутствующем предыдущем close использует `high-low`.
Отсутствующие high/low не заменяются нулями. У RSI пропуск source не создаёт
новую разность; следующий определённый source сравнивается с последним
определённым. При одних повышениях RSI=100, при одних понижениях RSI=0,
при нулевых средних роста и падения RSI=`na`. У highest/lowest пропуски занимают
место в окне, но не участвуют в выборе; неполное окно уже даёт доступный
экстремум, полностью пустое — `na`.

## Состояние и контекст

Каждое написанное место вызова имеет отдельное состояние. Вызовы в разных
пользовательских функциях и запрошенных контекстах также независимы.
Пропущенный условием вызов не обновляет его накопители; вычисляйте индикаторы
в глобальных переменных, если они должны видеть каждый бар. История OHLC
остаётся историей физических баров контекста.

Состояния сохраняются тем же checkpoint, что курсоры контекстов, позиция и
защитные заявки. Восстановление проверяет длину/тип накопителя и идентичность
программы. Память ограничена длиной окна и общим бюджетом runtime, а не числом
прочитанных свечей. Для незакрытой свечи действует существующий rollback
вычислительного состояния. Защитный брокер по-прежнему допускает только
подтверждённую историческую OHLC-модель.

В третьем аргументе `security`/`request.security` доступны ограниченные
числовые выражения: OHLCV, арифметика, уже поддержанные числовые функции и
новые TA, фиксированные входные параметры и история OHLCV/source-input
с литеральным смещением `0…100000`. История результата вычисления не поддержана.
Они вычисляются на барах запрошенного контекста. Максимум — 128 узлов
выражения и глубина 16; подробные ограничения — в [CONTEXTS.md](CONTEXTS.md).
Вычисление входного `source` также относится к запрошенному контексту.

## Три семейства примеров

Каталог [examples/technical](../examples/technical/) содержит по три программы
для каждой версии, 240 синтетических минутных OHLCV и 60 четырёхминутных баров.
Данные намеренно имеют растущие и падающие участки. Старшие свечи заранее
агрегированы в самом fixture; runtime не достраивает и не ресемплирует данные.
Происхождение записано в [fixtures.json](../examples/technical/fixtures.json).

| Семейство | Сигнал и фильтр | Защита |
| --- | --- | --- |
| `atr-cross-vN.pine` | Пересечение SMA/EMA, ATR для расстояния до уровней | Цены фиксируются на закрытии сигнального бара при отправке входа |
| `rsi-trend-vN.pine` | RSI пересекает 50 вверх, close выше SMA | Фиксированный ATR-bracket; отдельное закрытие при падении RSI ниже 45 |
| `channel-filter-vN.pine` | Пробой предыдущего highest, фильтр RMA типичной цены старшего контекста | Stop по lowest и limit по close+3×ATR заменяются на каждом закрытии |

Во всех программах фиксированное количество 1, одна long-позиция, полный
защитный выход, капитал 1000 и комиссия 0.1%. Это демонстрационные параметры.
Индикаторы считаются с начала снимка; вход требует определённых значений.

**Фиксированная защита** использует цену сигнального close, известную при
создании заявки. Фактический вход происходит на следующем open и может
отличаться из-за гэпа. Это не фиксация уровней относительно ещё неизвестного
fill. **Пересчитываемая защита** вступает в силу со следующего бара; stop
может и повышаться, и снижаться. Она не является встроенным trailing order.

В v1/v2 `security` имеет исторический lookahead on без отдельного переключателя.
В примере канала это отмечено прямо в исходнике: финальное значение старшей
свечи видно до её закрытия. Такие результаты нельзя считать проверкой без
чтения будущего. В v3–v6 default lookahead off сохранён.

```powershell
openpine job --source examples/technical/atr-cross-v6.pine --bars examples/technical/bars.json --state-dir .runtime/technical
openpine job --source examples/technical/rsi-trend-v6.pine --bars examples/technical/bars.json --state-dir .runtime/technical
openpine job --source examples/technical/channel-filter-v6.pine --bars examples/technical/bars.json --contexts examples/technical/contexts.json --state-dir .runtime/technical
```

Для v1–v5 замените суффикс версии. Все примеры используют обычные jobs, прежний
worker, Parquet, график и защитный журнал. Параметры доступны существующим
[экспериментам](INPUTS.md), [поиску](SEARCHES.md) и [walk-forward](RESEARCH.md).
Прогрев отдельного исследовательского окна задаётся явно; неизвестную историю
индикатор не восстанавливает. У прикладных примеров прибыльность не является
критерием корректности.

Готовые небольшие планы подбора используют те же локальные данные:

```powershell
openpine experiment run --source examples/technical/rsi-trend-v6.pine --bars examples/technical/bars.json --request examples/technical/rsi-experiment.json --state-dir .runtime/technical-research
openpine search run --source examples/technical/atr-cross-v6.pine --bars examples/technical/bars.json --request examples/technical/atr-search.json --state-dir .runtime/technical-research
```

`channel-walkforward.json` — состав одного fold. Дополнительный снимок сначала
сохраняется у владельца каталога. Например, запустите в отдельном терминале
`openpine serve --port 8765 --state-dir .runtime/technical-server`, затем
подготовьте request и отправьте его через существующий CLI-клиент:

```powershell
@'
import json
from pathlib import Path
from apps.openpine.http_client import LocalClient
p = Path("examples/technical")
client = LocalClient("http://127.0.0.1:8765")
contexts = json.loads((p/"contexts.json").read_text())
snapshot = client.call("/api/snapshots", {"provider":"upload", "raw":(p/"higher.json").read_text()})
request = json.loads((p/"channel-walkforward.json").read_text())
request.update(primary_context=contexts["primary_context"], contexts=[{"context":contexts["contexts"][0]["context"], "snapshot_id":snapshot["sha256"]}])
Path(".runtime/technical-channel-request.json").write_text(json.dumps(request), encoding="utf-8")
'@ | python -
openpine walkforward run --source examples/technical/channel-filter-v6.pine --bars examples/technical/bars.json --request .runtime/technical-channel-request.json --server http://127.0.0.1:8765
```

CLI загрузит основной снимок и скомпилирует исходник в том же сервере;
дополнительные ссылки соответствуют [контракту контекстного задания](CONTEXTS.md).
`tools.installed_technical_smoke` проверяет эту композицию через HTTP,
включая раздельный holdout, сохранённые контексты и хеш отчёта.

## Проверки

Независимые числовые ожидания находятся в `conformance/technical/cases.json`;
границы накопителей — в `tests/test_technical.py` и `tests/test_technical_state.py`; запрошенные выражения и
изоляция контекстов — в `tests/test_request_expressions.py`; сквозные задания,
исследования и восстановление — в `tests/test_technical_pipeline.py`.
Инвентаризация API: `python -m tools.capabilities --json`.
Только новые рациональные числовые fixtures допускают `abs/rel <= 1e-12`
для округления IEEE float. Прежние сценарии сохраняют точное сравнение.
Статус полной квалификации фиксируется отдельно в [TECHNICAL_PLAN.md](TECHNICAL_PLAN.md).
