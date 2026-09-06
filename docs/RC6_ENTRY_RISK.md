# OP-02 / OP-07 / OP-10 / OP-14 / OP-20 — entry risk controls

## Supported compiled API

`strategy.risk.max_position_size(contracts)` and
`strategy.risk.allow_entry_in(value)` use the existing, schema-validated RiskIntent
2.2.0 and the engine-owned command registry. They are not translated into synthetic
entry orders and do not introduce another wire schema. The original seven trading
commands and 17 scalar strategy values remain present.

The supported host form is an unconditional global rule, with parameters fixed for
the run (including inputs). Conditional/local/UDF risk calls are rejected explicitly:
Pine risk commands must not become ordinary branch-dependent controls. Dependency-safe
extraction of arbitrary declarations has not been implemented. One direction rule
per compiled script is currently admitted; multiple potentially conflicting direction
rules are rejected rather than assigned an undocumented precedence. Multiple position
limits combine by their minimum. Missing/NA/bool/nonfinite/negative numeric limits are
rejected; zero is a real no-new-exposure limit. Risk commands do not accept `when`,
including in historical versions. Other risk commands remain unbound.

```pine
//@version=6
strategy("Entry controls", pyramiding=2)
cap = input.int(3, minval=0)
strategy.risk.max_position_size(cap)
strategy.risk.allow_entry_in(strategy.direction.long)
if bar_index == 0
    strategy.entry("A", strategy.long, qty=10)
if bar_index == 2
    strategy.entry("B", strategy.short, qty=1, limit=500)
```

On suitable data the first entry is limited to three units. The prohibited opposite
entry closes the full existing position at market instead of creating a short entry,
closing just one unit or waiting for the supplied limit. Its metadata is retained.
This example does not imply automatic acceptance of every Pine signature or market.

## Actual exposure, not sum of pending requests

The same broker function limits entry quantity at creation/amendment and immediately
before a real fill. Two pending entries of 3 and 4 under a cap of 5 may fill as 3 and
2. A pending order cannot grow again after clipping or OCA reduction. Capacity is
rounded down to the configured quantity step and must meet minimum quantity; it is
never rounded above the risk cap. No-capacity orders are cancelled before commission,
position or fill records are produced; their deferred exits are cleared.

Only `strategy.entry` is affected by these two rules. `strategy.order`, exits and
explicit closes keep their existing semantics. For reversal the closing component
is separate from desired new-direction size: closing eight and opening three can
require an eleven-unit transaction without violating a three-unit resulting position
cap. A previously queued full close must not add that closing quantity twice.

Native callbacks can apply direction rules after orders have been queued. Pending
prohibited entries are cancelled on a flat/same-direction position or reconciled into
market-only full closes of an opposite position. This native update behavior is tested;
it is not a claim that Pine permits dynamically changing series-valued risk parameters.
Existing fill timing, protection and metadata paths remain owned by the broker.

## Parser / input evidence

Intermediate namespaces are derived only from qualified names present in the active
version catalog. This fixes missing v5 `strategy.direction` evidence without importing
unknown members or later-version names. The two risk argument qualifiers are normalized
to `simple` in the catalog generator and all sealed packs are regenerated. Scalar
legacy `input(...)` keeps the `input` qualifier; `input.source` and legacy source inputs
remain `series`, so price data cannot masquerade as a fixed rule parameter. Positive
and negative compiler tests cover versions 1–6. These tests are implementation evidence,
not independent historical TradingView execution or a full signature census.

## Risk state and compatibility

Native broker checkpoints and realtime rollback snapshots now retain the risk controls
that were active before the snapshot. Current snapshots have risk_state_version=1
and require complete, domain-validated flags/limits. Invalid data is checked before
replacement. A rule registered only before a checkpoint remains active after native
resume; fills, trades and equity are compared against uninterrupted execution.

Legacy version-0 snapshots without a risk section keep their compatibility path.
They cannot prove what dynamic risk rules were active when produced. This migration
path is not an authenticity guarantee: checksums and version fields are not signatures.
Full isolated broker/IPC/worker restart, generalized atomic recovery and complete
historical-to-realtime execution remain separate tasks. Existing native drawdown
configuration is preserved in snapshots, not newly certified as Pine drawdown parity.

## Verification and remaining work

New files contain 78 native broker cases, 17 intent-binding cases, 42 parser cases
and 45 host cases (four require protected processes). The shared registry also adds
two cases to an existing parameterized binding test. Former whole-order rejection and
partial prohibited-reversal expectations are corrected to the documented behavior,
not skipped. Test reports and publication status are recorded in the eventual receipt;
this implementation note does not claim unobserved CI completion.

Update the exact source set in RC6_LIFECYCLE_SOURCES.json and recompile artifacts
against the changed host surface. The source/dependency identity changes even though
package release strings still say RC6. The persistent CI retains all previous suites,
real workers, optimizer containment and frontend/API checks.

Still open: other risk rules (drawdown, intraday counts/loss, consecutive loss days),
arbitrary declaration extraction, multiple direction-rule conformance, full broker
FIFO/ANY and fixed/trailing stop arbitration, indexed trade APIs, all market-specific
quantity policies, complete version/type matrices and independent TradingView oracle
comparison. No new trading recommendation, speedup, coverage gate or production wheel
acceptance is implied. There is no timing or full-backtest throughput benchmark here.

Primary semantic references used for design, not external execution evidence:
- https://www.tradingview.com/pine-script-docs/concepts/strategies/#risk-management
- https://www.tradingview.com/pine-script-docs/v4/essential/strategies/#risk-management
- https://www.tradingview.com/pine-script-reference/v6/#fun_strategy.risk.max_position_size
- https://www.tradingview.com/pine-script-reference/v6/#fun_strategy.risk.allow_entry_in
