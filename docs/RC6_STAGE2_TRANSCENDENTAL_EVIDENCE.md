# Additional manual mathematical expected cases

The separately locked `verification/builtin-transcendentals-v1` corpus contains
44 cases for Pine v5/v6:20 finite tables,20 typed-NA tables and four out-of-domain
arcsine/arccosine tables. The ten families are acos,asin,atan,sin,cos,tan,
todegrees,toradians,log10 and round_to_mintick.

Expected values are literal elementary angle/logarithm calculations and exact
quarter-tick midpoints, authored before executing the implementation. No tested
runtime or compiler calculated expected files. Floating angle tables use a
1e-12 absolute tolerance; domain NA and quarter-tick rounding use exact values.
Eighteen NA tables are explicitly inferred from general documentation. The
round_to_mintick NA clause and arcsine/arccosine domain clauses are explicit
reference rules. This is engineering evidence, not TradingView server execution.

The [language reference](https://www.tradingview.com/pine-script-reference/v6/)
and [v5 reference](https://in.tradingview.com/pine-script-reference/v5/) provide
the function contracts. The corpus embeds per-case provenance links. Its lock is
`sha256:6550ec8f3e26a1ba295728ceb22c4c84e0ee8495122aec7f1586c50faa51bdd6`.

The shared runner now admits an authored InstrumentContext in both initial and
restored sessions. The first execution produced20 visible missing-instrument
errors, all in rounding cases; only this adapter was repaired. The44 source,
data, settings, expected and lock files remained unchanged. The previous47 and75
case corpora are preserved.

Five real paths (ABI,historical,realtime,abort/retry,checkpoint) produce220
execution checks plus one corpus check. All221 pass locally on each Python3.11
and3.13. Local working owner changes are not joint Linux acceptance: the exact
published source still needs the full eight-owner pipeline and unchanged
Stage1,sandbox,architecture and frontend gates. Full builtin and Stage2
acceptance remain false.
