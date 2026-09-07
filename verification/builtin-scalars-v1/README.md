# Independent scalar examples, revision 1

This separately frozen engineering corpus contains **75 manual cases**, with
five intended paths per case: ABI, compiled historical, compiled realtime,
compiled abort/retry, and compiled checkpoint continuation. It does not replace
`builtins-v1`, establish TradingView execution, or accept Stage 2.

Expected values were authored before SUT execution. The packaging helper used
only Python standard-library JSON, hashes and filesystem operations. It did
not import PineLib, either compiler, or a runner. These are literal manual
tables, not expected values copied from observations.

| Inputs / expression | Independently expected result |
| --- | --- |
| Integer abs: -7, 0, 7 (separate constant cases) | 7, 0, 7, preserving int ABI type |
| Float abs: -7.25, -0.5, 0.0, 0.5, 7.25 | 7.25, 0.5, 0.0, 0.5, 7.25 |
| ceil: -2.25, -2.0, -1.5, -0.25, 0.0, 0.25, 1.5, 2.0, 2.25 | -2, -2, -1, 0, 0, 1, 2, 2, 3 |
| floor, same inputs | -3, -2, -2, -1, 0, 0, 1, 2, 2 |
| float, signed fractional table | Same numeric values, float ABI type |
| float(-7) | -7.0, float ABI type |
| float(na) | Numeric Pine NA |
| na(close), signed table including zero | false on all five bars |
| na(na) | true on all five bars |
| na(close[1]) | true on first bar; false on four following bars |
| Typed numeric NA into abs(int), abs(float), ceil, floor | NA; explicitly inferred from general documentation |

Every finite fraction is exactly representable in binary; numeric tolerance is
zero. The ABI checks exact int/float/bool types before projection. Generated
Pine plots `na(...) ? 1 : 0`; the ABI applies the same explicit display encoding
only after requiring an actual bool. False, zero, Pine NA and JSON null remain
distinct at the comparison boundary. The history ABI input table is manually
listed independently of compiled history storage.

The corpus's engineering NA token is `{"$na": true}`. The runner explicitly
converts it to PineLib's canonical NA value for ABI inputs; it is distinct
from the runtime checkpoint transport token `{"$pine": "na"}`. Ordinary false,
zero and null are not converted by that fixture adapter. The initial ABI run
exposed a missing adapter (27 failures); expected files and their lock remain
unchanged after fixing the runner.

Each source locks the exact symbol, overload and call form. `abs(int)` and
`abs(float)` use distinct overload IDs. Finite scalar/NA-predicate cases cover
Pine v1-v6; explicit `float()` cases cover v4-v6, following its documented
introduction. Typed mathematical NA examples are limited to v4-v6 and labelled
`INFERRED_FROM_GENERAL_DOCS` in every affected case. There is no claim that the
general rule is an individually documented NA clause for those functions.

Primary references: [v4 casts and typed NA](https://www.tradingview.com/pine-script-docs/v4/language/type-system/#type-casting),
[NA values](https://www.tradingview.com/pine-script-docs/language/type-system/#na-value),
[general mathematical NA propagation](https://www.tradingview.com/pine-script-docs/v4/essential/arrays/#calculations-on-arrays),
and [scalar function definitions](https://www.tradingview.com/pine-script-reference/v6/).
Historical spelling follows the producer's version catalogue and remains a
separate identity check, not independent proof of historical server behavior.

`manifest.json` freezes the source, input data, settings and expected file
checksums. `lock.json` records its content hash. Do not regenerate expected from
the runtime or silently replace the lock after a failure. Execution reports
retain every installed callable signature, including missing and unavailable
rows; a passing example does not prove complete coverage of its function.
