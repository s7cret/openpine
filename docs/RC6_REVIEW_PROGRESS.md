# RC6 review progress — 2026-09-06

This ledger records implemented and verified scopes, not completion of all 36 review tasks or TradingView 1:1 compatibility.

## Latest published request / checkpoint / capability block

OpenPine runtime head: `53f0c2d6da67bf364e0962fb50a7107571182427`.
Joint CI and publication `33998764352`: **3,246 Python cases per interpreter (3.11 and 3.13), 152 Vitest cases and 22 Node cases**, all passing. Four new actual protected-worker request cases ran. OpenPine wheel/sdist and frontend production builds passed. The first five library functional suites are complete; provider excludes five live-network cases, and OpenPine uses native plus selected regressions. No complete standalone optimizer/coverage/oracle acceptance is claimed.

[RC6_REQUESTS_CHECKPOINT_PUBLICATION.md](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md) records the six exact OpenPine commits, published sibling pins, artifact digests and remaining limits. Both earlier blockers, A2P_PINELIB_INJECTION and na(strategy.position_avg_price) type evidence, are resolved for the documented supported subset. The former unconfirmed-publication status is superseded by actual joint CI, publication receipts and fresh remote reads.

- OP-08: independent compiled source expressions, typed lower-TF arrays, explicit snapshot admission, static preflight and no-lookahead causality tests. Automatic UI/CLI series discovery, nested/UDF and live requests remain open.
- OP-10: real generated-state export, atomic restore, NA round-trip and receipt-derived counters. Full isolated-process broker/IPC resume remains open; v1 generated envelopes without receipts are rejected.
- OP-14: request/NA/array type and version bindings are improved, not the entire Pine v1-v6 catalog.
- OP-15: worker advertises and negotiates actual closed-bar support, not unimplemented checkpoint resume. The full producer/compiler/runtime/host capability graph is not closed.

Update all pinned sources together using [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json), and recompile artifacts against the new target manifest. Same version strings do not establish identical code. The retained read-only native CI now includes complete Pine2AST verification and new request/checkpoint/capability lint paths; runtime and UI code are unchanged by this ledger/CI update.

## Preserved branch state

OpenPine still has main, release/v2.17, release/v4.0.2 and release/5.0.0rc6. The completed request publication archived its temporary branch as a same-name tag before removal. Main and historical release heads were untouched. Earlier preservation evidence remains in [RC6_BRANCH_CONSOLIDATION_RECEIPT.md](RC6_BRANCH_CONSOLIDATION_RECEIPT.md) and [RC6_BRANCH_SELECTION.json](RC6_BRANCH_SELECTION.json). Cleanup of all sibling branches is not claimed.

## Previous verified blocks (historical counts overlap)

- [Data and UI publication](RC6_DATA_UI_PUBLICATION_RECEIPT.md): strict offline input, chart timeframe/pointvalue, stale-response protection and display-only downsampling. Its request-compilation failure notes are historical and superseded by the new request block.
- [Strategy host publication](RC6_STRATEGY_PUBLICATION_RECEIPT.md): seven commands and 17 scalar values; exits/risk/indexed methods remain partial.
- [Cleanup / bounded results / progress](RC6_CLEANUP_EXECUTION_BLOCK.md): strict Parquet, no legacy pickle, sealed result chunks, explicit msgpack worker policy, input framing and progress.
- [Lifecycle integration](RC6_LIFECYCLE_BLOCK.md): fill recalculation, history commit boundaries and rounding/margin transport.
- [Inputs / optimizer](RC6_INPUTS_BLOCK.md): tested input overrides and effective trial settings.
- [First publication](RC6_REVIEW_PROGRESS_FIRST_PUBLICATION.md): historical evidence, superseded where later blocks explicitly say so.

## Remaining high-priority acceptance

Automatic requested-series loading, unsupported request forms/live data, full strategy exit/trailing/risk/indexed access, full-job recovery, immutable production delivery, cancellation/backpressure, complete version-specific signatures and capability coverage, browser UX and Pine v1-v6 TradingView oracle coverage remain open. No full 36-task acceptance, whole-backtest speedup or production release is claimed.
