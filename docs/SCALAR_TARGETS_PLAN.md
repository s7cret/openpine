# Этап: чистые scalar-функции и цели запросов

1. Закрепить Wiki v1–v6: имена, сигнатуры, qualifiers, доступность и пробелы свидетельств.
2. Ввести общий закрытый реестр pure scalar ABI, effects и стоимости для runtime и resolver.
3. Добавить ограниченные tostring/str.tostring, length, substring, contains, replace_all; определить формат, Unicode, пустые строки и NA.
4. Добавить in_seconds/from_seconds v5/v6, единицы, округление, пределы и default primary period.
5. Допустить явный whitelist в новом профиле computed/UDF targets; проверить весь граф до I/O, сохранить старые профили.
6. Проверить независимые оракулы, длину до аллокации, общий бюджет вложенных вызовов, worker/research/replay/live/recovery.
7. Провести полную приёмку Python 3.14.8, Windows/Linux, installed wheel и миллион событий; сохранить 1105 прежних test ID и 2214 наблюдений; опубликовать Git и локальную отметку Wiki.

Реализованные правила: [SCALAR_TARGETS.md](SCALAR_TARGETS.md).
Приёмка будет закреплена исходным commit и оригинальными квитанциями запусков.
Точное сравнение с TradingView остаётся отдельным этапом.
