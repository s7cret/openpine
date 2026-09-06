# OP-07 / OP-20: versioned trailing exits

Trailing activation and adverse stop crossings use the same forward-only price-event
scanner as fixed orders. Historical paths cannot use a high/low before entry or
activation. Newly attached all-entry policies observe the actual entry fill before
continuing the path. Gaps are discrete observed prices, not interpolated segments.

Each actual entry fill has its own activation, best observed price and reserved
quantity. All-entry persistence, named IDs, cancellation/replacement and partial
quantities use the existing scope/lifetime machinery. A repeated trail is modified
in place; an already active trail keeps its best price. Explicit offset amendments
use that best price rather than replaying historical bars. This is tested native
behavior, not an independent TradingView oracle for every amendment combination.

Pine v6 chooses the first activation reached from trail_points/trail_price. Versions
1-5 retain absolute precedence. Native callers choose price_pair_policy explicitly,
with absolute_first remaining the default. Distances use admitted mintick at the
actual entry fill. Zero offset is valid; missing offset is not inferred. NA fields
are absent values, not zero. Nonfinite/negative offsets are rejected.

The compiled bridge supports a trail alone or with a fixed take-profit. It still
rejects a fixed stop/loss and a trailing stop in the same call: arbitration between
those two stop mechanisms is not implemented in this subset. Per-leg messages,
full FIFO/ANY attribution and all historical signatures remain separate tasks.

ExitIntent 2.5.0 explicitly carries price_pair_policy plus complete trailing
parameters and exactly one target scope. Old 2.2/2.3/2.4 forms retain their validation.
The host and worker require compatible Engine/Contracts revisions and recompiled
artifacts. Matching package versions alone are insufficient.

Native broker snapshots retain trail_best_price and activation state. Resume tests
compare trades, fills and equity to uninterrupted execution. This is NOT full
isolated-job/IPC recovery, and no full OP-10 completion is claimed.

The new tests include 25 contract cases, 55 native broker cases and 28 compiled host
cases (four require real protected workers). Local checks use Python 3.13.5. CI
results are recorded only once observed in the publication receipt. Prior rejection
expectations are replaced by incomplete/unsupported-combination tests, not skips.
No live TradingView execution, timing, coverage threshold or production installation
acceptance is claimed. Full OP-07/20 acceptance remains partial.

Official semantic references:
- https://www.tradingview.com/pine-script-docs/concepts/strategies/#trailing-stops
- https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/#strategyexit-evaluates-parameter-pairs
