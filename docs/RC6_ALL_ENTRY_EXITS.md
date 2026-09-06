# OP-07 / OP-20 — all-entry exit lifetimes and relative per-fill brackets

## Implemented scope

An omitted or empty from_entry in strategy.exit now applies to open entries and already-submitted pending entries. Once attached to a position, the instruction persists for later same-direction entries until that position becomes flat or reverses. Cancellation, cancel_all and replacement clear the relevant policy and obsolete children. A call while flat without a submitted entry does not arm an unrelated future position.

The native broker owns this lifecycle. The host does not expand one Pine command into synthetic entries or duplicate broker logic. The wire contract introduces an explicit all_entries scope in intent version 2.3.0. Named exits remain strict version 2.2.0 and require from_entry; malformed old records do not acquire wildcard semantics. Entry IDs such as *, A:B and all_entries remain literal user identifiers.

Unqualified exits and relative profit/loss exits are materialized per actual opening fill, rather than per shared entry ID. Relative distances use each fill price and the admitted mintick. Per-fill quantities use the original entry size, respecting already-reserved and remaining quantity. A later fill sharing an ID does not reuse a consumed partial exit from an earlier fill. Default bracket OCA reduction is local to its target lot; explicit OCA groups retain their separate cross-order semantics.

The first child retains familiar X:L/X:S labels; additional children have deterministic fill-qualified labels. Execution identity comes from explicit fields, not parsing those labels. Public parent IDs, including colons, are preserved in native resume and snapshot state.

Replacement cancels obsolete targets and price legs, including switches between all-entry and named scopes or absolute and relative levels. Opposite ordinary order commands can reduce a reserved position instead of recording a phantom non-reducing fill. The forward-only price scanner from the prior price-entry block is retained.

## State and verification boundaries

Position-lifetime policies are included in native broker resume/realtime snapshots and are detached on copy. Warmup reset removes them. New native resume tests compare exact fills, trades and equity with uninterrupted execution for long and short positions. This does not implement isolated-worker/IPC restart recovery, nor does it change the worker's advertised closed-bar capabilities.

Contract tests cover valid and invalid versions/scopes and generator consistency. Broker regressions cover long/short, on-close and fill-recalculation flags, pending entries, subsequent fills, cancellation, replacement, per-fill reserves and lifecycle reset. New compiled Pine cases compare both transports; four mandatory real-worker cases cover the new contract with both on-close settings. Publication receipts record actual CI results separately. A test path here is not a claim of an unobserved successful run.

## Deliberate remaining limitations

Named absolute exits retain the existing aggregate quantity behavior. Full FIFO/ANY allocation and trade-report attribution, including their interactions with repeated entry IDs, remain separate acceptance work. This per-fill implementation is not a complete proof of all TradingView trade-matching semantics.

Trailing, mixed relative/absolute price pairs in Pine v6, per-leg metadata, risk commands, indexed trade methods, complete realtime/margin/commission interactions and the independent TradingView execution oracle remain open. Broad OP-07/OP-20 acceptance and all-36 closure are not asserted.

Per-lot brackets may increase active order count and scanning work. No speed, memory or whole-run performance improvement was measured. Examples and tests use synthetic expected outcomes, not captured TradingView results.

## Upgrade

Update host, worker, Backtest Engine and Contracts together using RC6_LIFECYCLE_SOURCES.json. Recompile artifacts for the changed host surface. Matching package version strings are insufficient; old readers do not understand 2.3.0 all-entry intents. Existing valid 2.2.0 named intents remain unchanged. This source verification is not an immutable standalone production installer.

Reference semantics: https://www.tradingview.com/pine-script-docs/concepts/strategies/
