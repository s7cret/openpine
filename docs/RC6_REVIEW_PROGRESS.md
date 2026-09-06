# RC6 review progress — 2026-09-06

This ledger records implemented and verified scopes, not completion of all 36 review tasks or TradingView 1:1 compatibility.

## Latest published nested-request reconciliation

OpenPine source head: `17430a5638d299245b19d30c0d6f5a0166af6ff0`.
PineLib source: `cddaa0bf2324db36b4cd29e944a251032658c59d`; Ast2Python source: `51552e7c1de339d8de4794290655e574d6447e82`.
Joint CI/publication `34001163455` passed **3,315 Python cases on each of 3.11 and 3.13**, 152 Vitest cases, Node checks, actual frontend build and Python wheel/sdist builds. New real protected workers verify nested expressions and multi-frame preloads. Both library publications preserve the original local commit SHAs.

[RC6_NESTED_MERGE_PUBLICATION.md](RC6_NESTED_MERGE_PUBLICATION.md) records exact source commits, independent artifact checks, publication retry and four-branch inventory. [RC6_NESTED_REQUEST_MERGE.md](RC6_NESTED_REQUEST_MERGE.md) documents behavior and limits. The old local OpenPine tree was not written over RC6. Published checkpoint v2 receipts, worker capability negotiation, numeric-underflow checks and their regression files were retained byte-for-byte.

OP-08 now includes supported historical nested requests with enclosing-context preflight and shared request budgets/cache/rollback. One public request_manifest API is retained; its bounded chunk transport replaces large bootstrap JSON. Dynamic unknown contexts are not guessed. Auto-loading from UI/CLI/gateway, UDF/live requests and static nested-syntax extraction with dynamic_requests=false remain open. Full isolated-job restart is still not implemented. The prior optimizer containment diagnostic and dependency audit notices remain unaddressed, not hidden by the successful functional suite.

Five provider live-network tests were explicitly excluded; full OpenPine, standalone optimizer, coverage, browser visual and external TradingView oracle acceptance is not claimed. Historical test counts below overlap and must not be added.

## Previous numeric-boundary verification

Code head: `0ab826a7a71a6010f0eed680405cdc36b68bc88a`.
Permanent read-only CI `33999714708` passed **3,277 Python cases per interpreter (3.11/3.13), 152 Vitest cases and 22 Node cases**. Actual protected worker tests, OpenPine wheel/sdist, TypeScript and production UI builds passed. The OP-04 commit prevents finite nonzero canonical decimals from silently becoming zero in both worker paths and request preloads, with 31 additional regressions. Genuine zero and representable subnormals remain valid.

See [RC6_NUMERIC_REQUEST_VERIFICATION.md](RC6_NUMERIC_REQUEST_VERIFICATION.md) for exact test scope, artifact digests and unresolved diagnostics. A separate local standalone optimizer containment probe failed and remains unaccepted. That frontend install reported one high and one low dependency vulnerability, not investigated by this merge. Functional CI success is not a complete optimizer, security or release gate.

## Preserved request / checkpoint / capability block

The six source commits through `53f0c2d6da67bf364e0962fb50a7107571182427` are already published; the later changes do not replace them with the older recovery copy. Their original joint verification/publication `33998764352` passed 3,246 Python cases per interpreter, 152 Vitest and 22 Node cases.

[RC6_REQUESTS_CHECKPOINT_PUBLICATION.md](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md) records exact source commits, sibling pins, publication receipts and historical limits. Both former blockers, A2P_PINELIB_INJECTION and na(strategy.position_avg_price) type evidence, are resolved for the documented supported subset. Its nested-request restriction is superseded by the latest scoped implementation above.

OP-10 retains real generated-state export, atomic restore, NA round-trip and receipt-derived counters. Full broker/IPC/process resume remains open; v1 generated envelopes without receipts are rejected. OP-14 improves request/NA/array type and version bindings, not the whole catalog. OP-15 negotiates implemented closed-bar support, not unimplemented checkpoint resume; the full cross-library capability graph remains open.

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
