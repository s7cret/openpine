# Cash settlement in the reporting currency

Run the supplied strategy and pass its returned job ID to the ledger reader.

```sh
python -m apps.openpine job --source examples/account-ledger/partial.pine --bars examples/account-ledger/bars.json --state-dir account-demo
python -m apps.openpine account-ledger --id JOB_ID --state-dir account-demo --currency EUR --fx-rates examples/account-ledger/rates.json
```

This historical reporting profile converts the broker's initial capital at the
first journal fill or mark. It settles each signed fill and its fee at the
daily FX observation available at the journal timestamp. Settled cash stays
in the reporting currency. At each bar the journal's marked native exposure
(equity less cash) is translated at that bar's rate. Native broker sizing and
the original simulation journal remain the inputs to the report. The output
identifies the job, candle snapshot and normalized FX snapshot. A job with its
own FX snapshot uses it automatically. A separately supplied snapshot must
match it. Use `--start` and `--limit` to select a range of bar indices.

| Operation | USD → EUR | Signed fill in EUR | Fee in EUR | EUR cash | Exposure | Equity |
|---|---:|---:|---:|---:|---:|---:|
| Initial capital 1000 USD | 1 | 0 | 0 | 1000 | 0 | 1000 |
| Buy 4 at 100 USD, close 105 | 0.8 | −320 | 3.20 | 676.80 | 336 | 1012.80 |
| Sell 2 at 110 USD, close 108 | 0.9 | 198 | 1.98 | 872.82 | 194.40 | 1067.22 |
| Sell 2 at 90 USD | 1.1 | 198 | 1.98 | 1068.84 | 0 | 1068.84 |
