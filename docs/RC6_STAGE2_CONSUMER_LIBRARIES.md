# Stage 2 — закреплённые библиотеки в обычном compile/validate

## Статус и база

Локальное продолжение `18981fb0b5ee9cfc3c4aa0a2986ca685854cf647` поверх
накопительных explicit-method и numeric/index изменений. Публикация, PR, remote
refs и Actions не выполнялись. Stage 2 остаётся `in_progress`; закрывается
реализация потребительского пути IMPORT-04, не полная приёмка всех импортов.

## Исправленный дефект сохранения

Предыдущий NativeRC6CompilerAdapter мог успешно скомпилировать Pine с библиотекой,
но ArtifactStore.save_artifact отвергал его: `source differs from library context`.
Скомпилированный bundle содержит виртуальный текст после связывания, а хранилище
верифицировало его относительно оригинального корневого Pine. Сквозное сохранение
через обычный CLI/API вследствие этого не работало, хотя in-memory тесты проходили.

Теперь общий источник PreparedSource сохраняет оригинал и связанный текст отдельно.
При чтении/записи ArtifactStore восстанавливает проверенный LibraryQualifierContext
из sealed consumer bundle, строго сравнивает оригинальный root с отдельно сохранённым
source.pine, а consumer проверяет по виртуальному тексту. Dependency hashes и
`@linkage` сравниваются с восстановленной receipt. Нельзя исправить ошибку, просто
подставив незапечатанный compile_meta вместо исходного текста.

Проверяются модификации root, транзитивного исходника, receipt, карты зависимостей и
повторно захешированной карты проекции. Прежние проверки artifact/source-map/code
идентичностей не отключены. Старые форматы bundle/artifact/исходной source map не
менялись; проекция добавлена отдельным полем metadata.

## Один путь подготовки, компиляции и сохранения

`openpine.compile.library_inputs` принимает явный lock, его заранее закреплённый
expected hash и точные исходники. Schema, reference, path и содержательные проверки
принадлежат существующему LibraryStore. Поиск latest, соседнего checkout или загрузка
исходников из worker не добавляются. Сохраняются ограничения 64 публикаций, 1 МБ на
исходник и 8 МБ суммарно. Хеш определяет согласованность входа, не доказывает автора.

`source_context.prepare_source` и NativeRC6CompilerAdapter используются и compile,
и validate. `persist_compile_result` — единый владелец сохранения для Python pipeline
и HTTP; второй конвертер metadata в gateway удалён. Успех сохраняет реальный sealed
артефакт; неуспех сохраняет исходный Pine, диагностический текст и структурированные
diagnostics, но не исполнимый Python и не активный артефакт.

Идентичность неуспешной компиляции включает исходник, параметры, producer metadata,
запрошенную идентичность lock и диагностику. Разные неисправные зависимости не
перезаписывают результат друг друга. `requested_library_lock_hash` — поле аудита
запроса, не доказательство допуска; `library_lock_hash`/linkage появляются после
подготовки. Для успешного артефакта по-прежнему учитываются лишь достижимые зависимости:
посторонняя библиотека не инвалидирует результат, изменение транзитивной — инвалидирует.

## CLI

Для уже зарегистрированного исходника и ранее подготовленного lock:

```sh
openpine pine validate example \
  --library-lock /absolute/project/pine.lock.json \
  --expected-library-lock-hash sha256:<64 hex> --json-output

openpine pine compile example \
  --library-lock /absolute/project/pine.lock.json \
  --expected-library-lock-hash sha256:<64 hex>
```

Прежний `openpine pine pine-compile` сохранён как совместимый alias. Оба флага
требуются вместе. JSON validate не создаёт и не активирует артефакт. Ошибка Pine
заканчивается ненулевым exit code; ошибка compile остаётся доступна в хранилище.

Файлы читает прежний LibraryStore.from_directory: no-follow directory-relative
чтение; symlinks, traversal и специальные файлы не допускаются. После чтения
компиляция использует захваченные bytes и не открывает исходные пути повторно.
Чтение из directory требует POSIX O_NOFOLLOW, не объявлено переносимым на Windows.
Первоначальное создание lock из доверенных исходников выполняется существующим
LibraryStore.create; получать expected hash автоматически из входящего JSON нельзя.

Сохраняются прежние требования configured deployment и точных producer commits.
При установке из wheel их нужно задавать существующими OPENPINE_BUILD_COMMIT и
OPENPINE_PRODUCER_COMMITS_JSON из согласованной поставки. Новый resolver не заменяет
release doctor и не ищет Git-вершины в произвольных каталогах пользователя.

## HTTP и очереди

POST `/api/pine/{source_id}/compile` и `/validate` принимают необязательный body:

```json
{
  "libraries": {
    "lock": {"schema_id": "pine2ast.library_lock.v1", "libraries": [], "content_hash": "sha256:..."},
    "sources": {"owner/Name/1": "//@version=6\nlibrary(\"Name\")\n..."},
    "expected_lock_hash": "sha256:..."
  }
}
```

Это схематический пример: libraries должен содержать действительные descriptors
всех переданных публикаций и вычисленный при осознанном pinning хеш. Пустой список
не является допустимым lock. Сервер принимает inline text, **не серверный path**.
Неизвестные поля и неверные типы запрещены; несовпадающий lock/source hash отклоняется
до постановки compile на выполнение. Старый запрос без body работает для обычного
Pine без импортов. Для import без lock возвращается явная P2A_LIBRARY_REQUIRED.

Gateway захватывает исходник, producer map и detached library store до запуска
BackgroundTasks. Тяжёлый compile выполняется через threadpool, а сохранение SQLite —
на прежнем event loop. Operation ID теперь UUID, не timestamp с коллизиями в одной
миллисекунде. Ошибка producer identity не допускает compile; validate возвращает
структурированный отрицательный результат, а не ложный успех.

После завершения gateway сверяет исходник с захваченным. При изменении/удалении
сохраняет исторический артефакт, но не делает его активным (`activated=false`).
Это проверка одного gateway-процесса, не SQL compare-and-swap между несколькими
процессами и не гарантия latest-request-wins для разных lock одного root. Полные
job leases, durable terminal state, отмена и crash-resume остаются этапом 4.

## Оригинальные координаты

ConsumerBundleError сохраняет detached frontend diagnostics. Пустая ошибка раннего
admission не вызывает глубокое копирование до ограничения входа: прежние defensive
тесты cycle/oversize/deep сохранены и прошли после устранения выявленной регрессии.
LibraryError несёт original source/line/column; синтаксическая ошибка библиотеки
теперь не получает искусственную строку 1.

Frontend span и compiler finding с точным node_id проецируются обратно в original
source. Неизвестный node ID или сообщение без координат остаётся явно unmapped;
место не угадывается по тексту имени. Compiler fault-injection проверяет маршрут
structured findings, а обычные type/syntax ошибки воспроизводятся реальной цепочкой.

`original_source_projection` — sidecar к существующей source_map. Он связывает
Python entry/node ID, виртуальный span и начало в оригинальном файле. При чтении
артефакта sidecar проверяется повторным выводом из sealed context, а не одним хешем.
Координаты — нормализованный Unicode Pine (строка/столбец от 1), не UTF-8 byte offsets;
переименованный токен указывает на исходное начало. Полная runtime traceback и
browser/editor-подсветка не реализуются этим блоком.

LinkedSource.original_locations выполняет пакетную индексированную проекцию: receipt
проверяется один раз, диапазоны ищутся бинарным поиском, индексы строк строятся один
раз на исходник. Single-offset API использует тот же механизм. Это устранение
повторной работы, не измеренное ускорение всего compile/backtest.

## Сквозные проверки

Проверяются настоящий CLI, HTTP через TestClient, source/artifact SQLite stores,
inline/directory/Python inputs, удаление исходных файлов после capture, сохранение и
readback артефакта, изменение достижимой/посторонней зависимости, побочные подмены,
оригинальные diagnostics, старые клиенты, ранний отказ admission и stale activation.

Брокерный сценарий хранит и заново читает библиотечный артефакт, затем получает два
счётчика с шагами 1 и 10: на третьем баре значения 3 и 30. Реальный entry qty=33
исполняется при price=102 на закрытии или 103 на следующем открытии. Pine 5/6,
calc_on_order_fills, обе транспорта сравниваются по сделкам/командам/капиталу.
IPC в этом harness намеренно in-memory и не называется protected execution.
Четыре отдельные теста требуют настоящего Bubblewrap worker и остаются обязательными.
Checkpoint-helper получает явно заданное broker-facing position_size, поскольку
не содержит брокера; фактические сделки проверяются отдельно реальным движком.

## Приёмка и ограничения

IMPORT-04 — `implemented_local_pending_joint`, не accepted. Исходные 16 пакетов,
четыре критерия Stage 2 и все предыдущие test IDs сохраняются. Same-version профиль
v5/v6, ограничения межверсионных импортов и все прежние ограничения explicit-method
блока остаются. Runtime/broker semantics не переписаны.

Окончательные числа локальных прогонов записываются отдельной receipt. Новый совместный
GitHub CI, Python 3.11, browser-проверка и полный численный evidence index не выполнены.
Прежний индекс с UNVERIFIED ожиданиями str.tonumber v5 не переводится в успешный и не
получает результат старых source pins. Это не завершение Stage 2/TradingView 1:1.
