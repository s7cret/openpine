# Session-based candle aggregation

Run from the repository root using Python 3.14.8.

```sh
python -m apps.openpine.market history aggregate --request examples/market-schedules/short-night-ohlcv.json --timeframe 3
```

The response includes the immutable snapshot ID. Use `--id ID` in place of
`--request FILE` for subsequent reads. `--start` is a zero-based aggregate
index and `--limit` is the number of aggregate candles (1–1000).

The target is a fixed-minute whole multiple of the source interval. Each
opening interval starts a new grid. The last bucket ends at session close.
Unscheduled snapshots use a continuous UTC grid. An absent source bar makes
all aggregate prices and volume unknown. A missing field on a present bar
affects only the aggregation that reads that field.

Three-minute reference values for the supplied one-minute snapshot follow.
Timestamps are milliseconds since the Unix epoch. The first bucket crosses
UTC midnight at 86400000.

| Start inclusive | End exclusive | Bars present / scheduled | Open | High | Low | Close | Volume |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 86280000 | 86460000 | 3/3 | 10 | 18 | 8 | 14 | 6 |
| 86460000 | 86640000 | 2/3 | null | null | null | null | null |
| 86640000 | 86700000 | 1/1 | 12 | 13 | 8 | 9 | 6 |
| 120000000 | 120120000 | 2/2 | 30 | 34 | 28 | 33 | 9 |

To inspect the second aggregate's one-minute candles, run the following command.

```sh
python -m apps.openpine.market history intrabars --request examples/market-schedules/short-night-ohlcv.json --timeframe 3 --bucket 1 --start 0 --limit 1
```

Use the returned `next` as `--start` until it equals `observed`. The two pages
contain timestamps 86460000 and 86580000. The missing minute occupies no
page entry. A scheduled bucket with zero observations returns an empty list.
