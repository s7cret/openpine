# RC6 review progress — 2026-09-06

This ledger records implemented and verified scopes, not completion of all 36 review tasks or TradingView 1:1 compatibility.

## Latest verified continuation

Code head: `0ab826a7a71a6010f0eed680405cdc36b68bc88a`.
Permanent read-only CI `33999714708` passed **3,277 Python cases per interpreter (3.11/3.13), 152 Vitest cases and 22 Node cases**. Actual protected worker tests, OpenPine wheel/sdist, TypeScript and production UI builds passed. The new OP-04 commit prevents finite nonzero canonical decimals from silently becoming zero in both worker paths and request preloads, with 31 additional regressions. Genuine zero and representable subnormals remain valid.

See [RC6_NUMERIC_REQUEST_VERIFICATION.md](RC6_NUMERIC_REQUEST_VERIFICATION.md) for exact test scope, artifact digests and unresolved diagnostics. A separate local standalone optimizer containment probe failed and remains unaccepted. The frontend install reported one high and one low dependency vulnerability, not investigated here. Functional CI success is not a complete optimizer, security or release gate. This ledger update is documentation only.

## Preserved request / checkpoint / capability block

The six source commits through `53f0c2d6da67bf364e0962fb50a7107571182427` are already published; the numeric continuation does not replace them with the older recovery copy. Their original joint verification/publication `33998764352` passed 3,246 Python cases per interpreter, 152 Vitest and 22 Node cases. Historical counts overlap the current suite and must not be added.

[RC6_REQUESTS_CHECKPOINT_PUBLICATION.md](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md) records exact source commits, sibling pins, publication receipts and limits. Both former blockers, A2P_PINELIB_INJECTION and na(strategy.position_avg_price) type evidence, are resolved for the documented supported subset.

- OP-08: independent source expressions, typed lower-TF arrays, explicit snapshot admission, static preflight and no-lookahead causality tests. Automatic UI/CLI data loading, nested/UDF and live requests remain open.
- OP-10: real generated-state export, atomic restore, NA round-trip and receipt-derived counters. Full broker/IPC/process resume remains open; v1 generated envelopes without receipts are rejected.
- OP-14: request/NA/array type and version bindings are improved, not the entire Pine v1-v6 catalog.
- OP-15: worker negotiates implemented closed-bar support, not unimplemented checkpoint resume. The full cross-library capability graph remains open.

Update the exact source set in [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json) together and recompile artifacts against the target manifest. Identical version strings alone are insufficient.

## Branch preservation

The four branches remain main, release/v2.17, release/v4.0.2 and release/5.0.0rc6. Main/historical heads were untouched. Completed maintenance history is retained in same-name archive tags. Earlier evidence remains in [RC6_BRANCH_CONSOLIDATION_RECEIPT.md](RC6_BRANCH_CONSOLIDATION_RECEIPT.md) and [RC6_BRANCH_SELECTION.json](RC6_BRANCH_SELECTION.json). Cleanup of every sibling repository is not claimed.

## Previous blocks

- [Data/UI](RC6_DATA_UI_PUBLICATION_RECEIPT.md): strict offline input, metadata and stale-response/display fixes; its request failure notes are historical.
- [Strategy host](RC6_STRATEGY_PUBLICATION_RECEIPT.md): seven commands and 17 scalar values; advanced exits/risk/indexed methods remain partial.
- [Cleanup/results/progress](RC6_CLEANUP_EXECUTION_BLOCK.md): real Parquet, sealed result chunks, explicit codec policy and progress.
- [Lifecycle](RC6_LIFECYCLE_BLOCK.md): fill recalculation, history boundaries and margin/rounding transport.
- [Inputs/optimizer](RC6_INPUTS_BLOCK.md): tested overrides and trial settings.
- [First publication](RC6_REVIEW_PROGRESS_FIRST_PUBLICATION.md): historical evidence, superseded only where stated.

## Open acceptance

Automatic and advanced requests, full strategy namespace, full-job recovery, immutable deployment, cancellation/backpressure, complete language signatures/capabilities, standalone optimizer, dependency audit, browser UX and external Pine v1-v6 TradingView oracle coverage remain open. No complete 36-task acceptance, measured whole-backtest speedup or production release is claimed.
