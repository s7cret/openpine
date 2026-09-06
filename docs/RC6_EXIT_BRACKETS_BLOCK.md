# OP-07 / OP-09 / OP-20: explicit market-entry brackets and exit sizing

## Implemented scope

The broker retains an explicit `strategy.exit` issued after an already-created
pending market `entry`/`order`, including within the same committed callback.
The instruction is attached to that order instance, not only its reusable public
ID. It is materialized after a real matching opening fill. A reducing opposite
order which opens no matching trade cannot create an unrelated bracket.
Cancellation and replacement update waiting instructions; a new order reusing a
cancelled entry ID does not inherit the old exit. Relative profit/loss levels are
based on the actual filled entry, not the submission candle close.

Pre-submitted brackets become eligible at the current fill price point. An entry
which gaps beyond an absolute TP or SL can therefore close at that same opening
point, even with script fill recalculation disabled. Existing script recalculation
and historical OHLC ordering are retained. This is documented OHLC emulation,
not tick-by-tick or external TradingView execution-oracle evidence.

Explicit exit sizing uses matching entry quantities rather than the entire
position. Initial entry size is retained when partial exits reduce remaining size.
Changing an existing exit's explicit quantity can reduce its reservation; it no
longer keeps the maximum old quantity. Reservation accounting sums pyramided trades
sharing an entry ID, rather than keeping only the last one. The existing partial
exit tests remain in the full broker suite.

The strategy bridge no longer rejects a valid entry+exit command batch based only
on the pre-callback open-position snapshot. Binding belongs to the existing broker.
A missing matching entry keeps the existing no-position diagnostic/no-op behavior.

## Admitted instrument tick

The new real Pine regression exposed a separate discrepancy: with admitted mintick
0.01 and no explicit broker tick, integer OHLC data could make the broker infer 1.
Pine's `profit=500` then used 500 price units instead of 5. Both parent and child
broker configurations now resolve the missing tick from the admitted context after
validating the submitted hash. Explicit conflicts, booleans, nonpositive,
nonfinite and float-underflow/overflow ticks are rejected before package staging.
The resolved tick participates in the effective configuration hash; the submitted
configuration is not mutated. A context-free standalone broker retains its existing
inference behavior. Other instrument defaults are not certified by this fix.

## Tests and exact source set

Backtest Engine source: `6b52242bd7887344386646ed9ab99227ca04d9df`.
It contains four separate commits on top of `70dd7ccfecaabafa3000d359770305acb32c8b9e`:
`f533c8555281422af2887f3d904032ced34ff4dc`,
`9492f3f411fbce1691fa3123620d62ede7bb1391`,
`6ca6a0deccdcc1922594834c881e675c8edcdc1c`, and the source head.

OpenPine runtime changes are `fe300627d26ac0216342bd397db41b676c593b4d`
and `df50d40b6cdf52f057c205463460bad10bb72858` on baseline
`ffee029e1fc44ab907ea61313f1bd37ef58d2989`.
`RC6_LIFECYCLE_SOURCES.json` pins the complete compatible stack.

Local Python 3.13.5 verification: the complete broker functional suite passes 622
cases, and the two new OpenPine files pass 41 non-process cases. Four new worker
cases require the actual Bubblewrap/AppArmor CI environment and remain mandatory;
no source-level skips or sandbox bypasses were introduced. Whole-stack CI results
are recorded in a publication receipt only after they are actually observed.

The 36 added broker cases include long/short, TP/SL, absolute/relative prices,
process-on-close and fill recalculation, cancelled/reused IDs, replacement,
reservation scope, repeated entry IDs and gaps. The 45 new host cases include
versioned named-when sources, full tape/trade/equity comparisons, exact admitted
ticks and four protected-worker runs. Local evidence overlaps later CI and is not
added to the unique-case total. No timing or memory improvement was benchmarked.

## Deliberate limits and compatibility

This does not close the full OP-07, OP-09 or OP-20 acceptance. The bridge still
requires explicit from_entry; all-entry/unqualified persistence, trailing and
per-leg metadata, v6 mixed relative/absolute selection, risk and indexed trade
methods remain open. Deferral for pending limit/stop/stop-limit entries is explicitly
rejected pending continuous-segment activation, not silently approximated. Deferred
relative exits across repeated entry IDs also fail explicitly because per-trade
price levels are not implemented. Omitted-leg replacement semantics were not changed.

The existing broker's same-ID weighted relative-level handling and bar magnifier
semantics are not comprehensively accepted here. The new initial-entry-size and
pending-instruction fields are plain snapshot data, but compatibility with old
broker resume snapshots is not established. Full isolated-job restart was not
implemented and no new worker checkpoint capability is advertised.

Update the host, worker and pinned engine together; recompile modules after the
host surface changes. An identical package version string is insufficient. Existing
nested request, request transport, checkpoint v2 and capability code is retained.
No main/historical release promotion, full 36-task closure, coverage certification
or autonomous production installer is claimed.

Reference semantics (not an execution oracle):
- https://www.tradingview.com/pine-script-docs/concepts/strategies/
- https://www.tradingview.com/pine-script-docs/release-notes/ (June 2022 qty_percent basis)
