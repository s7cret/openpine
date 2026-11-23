# OpenPine historical price profiles

The corporate request streams expose published event values. A separate market
history view applies events to immutable source candles. Its first version
supports raw prices and a backward split plus additive gross cash adjustment.
It identifies the source candle snapshot, normalized action dataset and as-of
time in the response. As-of defaults to the last source opening timestamp.

In the adjusted view an action applies to candle openings strictly earlier
than its effective instant once both the effective and publication times have
passed. A split divides historical OHLC by new shares per old share and
multiplies historical volume by the same ratio. Subtract the gross cash amount
per share from historical OHLC in the instrument's pinned currency. At a
shared effective instant apply the split first and quote the dividend per
new share. Later splits rescale earlier cash adjustments. The additive
profile permits negative adjusted prices. A missing source field stays null.
Both action streams must cover the requested history, including the as-of
instant. Calendar-independent comparisons use UTC milliseconds.

```sh
python -m apps.openpine.market history prices --request examples/corporate-actions/prices.json --price-profile split-cash --corporate-actions examples/corporate-actions/price-events.json --as-of-ms 150000
```

| Opening time | Raw OHLCV | Adjusted at 150000 |
|---:|---|---|
| 0 | 100,106,98,104,10 | 48,51,47,50,20 |
| 60000 | 52,54,49,50,18 | 50,52,47,48,18 |
| 120000 | 48,51,47,49,21 | 48,51,47,49,21 |

At 120000 the dividend is effective but unpublished. The first adjusted close
is 52, and the close at 60000 is 50. At 150000 they become 50 and 48.
