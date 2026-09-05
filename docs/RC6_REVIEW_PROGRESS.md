# RC6 review progress — 2026-09-05

This ledger records implemented and verified scopes, not completion of all 36 review tasks or TradingView 1:1 compatibility.

## Latest published data / chart metadata / UI block

OpenPine reviewed source: `5a900fb7728b02a37f03e2ac0cd8004dbb03efd2`.
MarketData Provider reviewed source: `4e269bb7e3d389cae6214da9e17898c32c7ead15`.
The exact ten source commits are published in the respective RC6 branches. Subsequent receipt commits change documentation only.

Joint CI `33991141948` verified **2774 Python cases per interpreter (3.11 and 3.13), 152 Vitest cases and 22 Node cases**. It includes protected worker processes, actual TypeScript/Vite build and current backend OpenAPI contract checks. Five provider live-network cases are excluded explicitly. Full OpenPine/Pine2AST/standalone optimizer/coverage/TradingView oracle coverage is not claimed. The initial Actions delivery-token failure was resolved by the authorized connector and a successful publication retry without altering tested source.

- **OP-21:** strict offline CSV/Parquet input, explicit timestamp/volume policies and bounded batch parsing. Indexing, complete import provenance, row-group pushdown and repeated intrabar I/O acceptance remain open.
- **OP-09:** provider hour/month/minute identity and admitted pointvalue survive both workers. Complete instrument metadata and non-crypto behavior remain open.
- **OP-28:** original visualization loadAll and parity-page races fixed; finite one-pass bounds, display-only pixel sampling and top-mismatch labels are tested. Seventeen actual Vue lifecycle tests are in-memory component tests, not browser/visual E2E. No FPS or whole-backtest speed claim.

Details and migration notes: [RC6_DATA_UI_PUBLICATION_RECEIPT.md](RC6_DATA_UI_PUBLICATION_RECEIPT.md). Pre-publication implementation notes: [RC6_DATA_UI_BLOCK.md](RC6_DATA_UI_BLOCK.md). Current source pins: [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json). Same-version wheels must not be mixed without exact identity admission.

## Preserved branch state

OpenPine still has exactly main, release/v2.17, release/v4.0.2 and release/5.0.0rc6. Maintenance branches are archived as same-name tags before removal. Main/historical releases are not promoted or rewritten. Earlier preservation evidence remains in [RC6_BRANCH_CONSOLIDATION_RECEIPT.md](RC6_BRANCH_CONSOLIDATION_RECEIPT.md) and [RC6_BRANCH_SELECTION.json](RC6_BRANCH_SELECTION.json). Cleanup of all sibling branches is not claimed.

## Previous verified blocks (historical counts overlap)

- [Strategy host publication](RC6_STRATEGY_PUBLICATION_RECEIPT.md): seven commands and 17 scalar values; OP-07 remains limited for exits/risk/indexed methods.
- [Cleanup / bounded results / progress](RC6_CLEANUP_EXECUTION_BLOCK.md): strict Parquet, no legacy pickle, sealed result chunks, explicit msgpack worker policy, input framing and progress.
- [Lifecycle integration](RC6_LIFECYCLE_BLOCK.md): fill recalculation, history commit boundaries and rounding/margin transport.
- [Inputs / optimizer](RC6_INPUTS_BLOCK.md): tested input overrides and effective trial settings.
- [First publication](RC6_REVIEW_PROGRESS_FIRST_PUBLICATION.md): historical evidence, superseded where later blocks explicitly say so.

## Remaining high-priority acceptance

Actual request.security/security_lower_tf probes still fail with A2P_PINELIB_INJECTION; request expression lowering, child-context execution and data integration must be implemented together. The separate na(strategy.position_avg_price) probe still fails type evidence. Immutable production deployment, full strategy exit/risk/indexed access, checkpoint/state-hash equivalence, whole-run cancellation/backpressure, comprehensive realtime, browser UX and Pine v1-v6 TradingView oracle coverage remain open. No production release or complete 36-task acceptance is claimed.
