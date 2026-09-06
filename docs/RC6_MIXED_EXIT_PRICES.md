# OP-07 / OP-20 — versioned fixed exit price pairs

## Implemented scope

Pine v6 commands containing active profit+limit or loss+stop pairs use one explicit
first_trigger policy per exit. The existing broker resolves candidates separately
for each actual entry fill: long TP selects the lower candidate and long SL the
higher; short mirrors these inequalities. Relative distances use the actual fill
price and admitted mintick, not the chart close or an average of separate lots.
Only one TP and one SL are materialized, preserving existing reservations/OCA,
pending market/limit/stop/stop-limit activation and position-lifetime policies.

Pine v1-v5 and old replay records retain absolute precedence. The wire format uses
ExitIntent 2.4.0 with price_pair_policy=first_trigger and exactly one named/all-entry
scope; at least one active pair is mandatory. Old 2.2.0 named and 2.3.0 all-entry
records remain valid but cannot carry the new policy. Other intent kinds are not
reinterpreted as 2.4.0. Native StrategyContext.exit defaults to absolute_first;
native users must explicitly request first_trigger because that API has no implicit
Pine-version context. Zero is a real distance; NA is omitted rather than converted
to zero. Recompile generated artifacts after updating the changed host surface.

Per-fill example: entries of 2 at 100 and 6 at 110, mintick=1, profit=20,
limit=125, qty_percent=50 produce exits of 1 at 120 and 3 at 125 in the synthetic
v6-policy regression. Historical absolute precedence selects 125. The tests are
explicit reference expectations, not an exported TradingView execution oracle.

## Verification boundaries

New regression cases cover both directions, TP/SL and both legs, zero/equal/NA
members, gaps and actual pending-entry prices, replacements, partial quantities,
repeated fills, native broker resume and the compiled Pine version matrix. Four
ordinary mandatory process tests cover interactive/bulk and on-close off/on with
fill recalculation. No sandbox bypass or test skip is added. Actual CI counts and
publication identities belong in the subsequent observed publication receipt.

The native full suite and local selected compiled-Pine tests are useful checks,
not substitutes for the two-interpreter integrated protected-worker run. No new
performance benchmark, coverage acceptance or browser visual test is claimed.

## Remaining acceptance

This block covers fixed profit/limit and loss/stop only. Trailing activation pairs,
FIFO/ANY attribution, aggregate named-absolute quantities, per-leg metadata,
risk/indexed methods, full historical optional signatures and all interactions
with margin/realtime remain separate work. OP-07/20 and the full 36-task review
remain partial. Existing all-entry/resume limits are unchanged; native broker
resume is not full isolated-worker/IPC crash recovery.

Install the exact source set in RC6_LIFECYCLE_SOURCES.json together. Old readers
cannot consume new 2.4.0 mixed exits, and matching package version strings do not
prove identical code. No production installer is supplied by this source block.

Official semantic reference:
https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/
