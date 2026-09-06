# RC6 review progress — 2026-09-06

The original all-36 inventory is preserved in RC6_REVIEW_36.json and RC6_REVIEW_36.md. Those files record their named snapshot base; newer publication receipts supersede only the implemented scopes they explicitly cover. Whole-task status is not a code or TradingView compatibility percentage: 29 partial, six unverified and one accepted (OpenPine branch consolidation).

## Latest verified trailing exit block

OpenPine tested code: `eb56964d75ff371a0755c34a3a92e146c1c78070`.
Engine: `38818c614a425902f881321faec311f03009c810`.
Contracts: `9aa381dc0c1570fda1ea90221d9148194e506279`.

Joint verification/publication `34041985679` passed **4,115 functional cases plus 37 review-accounting checks on each of Python 3.11 and 3.13**. Zero failures/errors/skips in executed selections. Four new actual protected-worker cases passed on each interpreter. Frontend: 152 Vitest + 22 Node cases, actual TypeScript/Vite production build and backend API checks. Six library suites are full; provider excludes five external-network cases and host is native plus selected regressions.

[RC6_TRAILING_PUBLICATION.md](RC6_TRAILING_PUBLICATION.md) records the four original source commits, independently checked archive hashes, restored Git bundles and successful publication retry. [RC6_TRAILING_EXITS.md](RC6_TRAILING_EXITS.md) describes the supported subset. The 108 new functional cases are 25 Contracts, 55 Engine and 28 host. Existing unsupported-case assertions became incomplete/unsupported-combination checks, not skips.

OP-07/20 now supports trailing alone and trailing with TP. Activation and active-stop crossings follow the shared causal price scanner; each actual entry fill has independent state and reserve. Persistent future entries observe their own fill price. Reissues amend the same order and retain its best price. Versioned activation uses first-trigger in v6 and absolute-first in the v1-v5 regression matrix. ExitIntent 2.5.0 explicitly transports the policy and complete trailing parameters; missing offsets are not invented and zero remains valid. Native resume preserves active trail state, not complete worker/IPC recovery.

The previous mixed-exit ledger's broad trailing gap is narrowed by this receipt. Still open: fixed stop/loss versus trailing arbitration in one call, independent amendment conformance, per-leg metadata, FIFO/ANY, remaining named-absolute repeated-entry quantity semantics, risk/indexed methods and full historical/realtime/margin matrices. OP-07/20 whole-task status remains partial.

This receipt/progress commit changes documentation only. It does not change tested runtime, UI or workflows; subsequent automatic CI results are not assumed.

## Preserved source and branches

Nine important host runtime files and the UI tree are byte-identical to the previous base. Requests, checkpoint v2, transport, canonical bars, config and optimizer are retained. Final publication receipts preserve main, release/v2.17, release/v4.0.2 and release/5.0.0rc6; main/historical heads remain unchanged. Temporary publication branches were archived as tags before removal. Cleanup of all sibling branches is not claimed.

Use [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json) as a coordinated source set and recompile generated artifacts; identical version strings do not identify identical code. Old readers cannot consume new 2.5.0 trailing exits. No self-contained production installer is provided by these evidence archives.

## Earlier verified work

- [Mixed prices](RC6_MIXED_EXIT_PUBLICATION.md): fixed price-pair semantics, v6 versus historical absolute precedence.
- [All-entry exits](RC6_ALL_ENTRY_EXITS_PUBLICATION.md): scoped lifetime and per-fill relative prices.
- [Price entries](RC6_PRICE_ENTRY_EXITS_BLOCK.md): chronological deferred entries, OCA and Bar Magnifier.
- [Entry brackets](RC6_EXIT_BRACKETS_BLOCK.md): deferred protection and admitted mintick.
- [Optimizer](RC6_OPTIMIZER_PUBLICATION_RECEIPT.md): independent trials, request rebinding, metric controls and strict ranking.
- [Nested requests](RC6_NESTED_MERGE_PUBLICATION.md), [checkpoints](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md) and [data/UI](RC6_DATA_UI_PUBLICATION_RECEIPT.md): preserved integration.

## Remaining major acceptance

Complete broker/strategy semantics, automatic request data discovery and unsupported UDF/live contexts, full-job restart, immutable delivery/doctor, winning-trial replay/holdout, full version-specific conformance and browser UX remain open. No speedup was measured. Passing regressions, explicit limitations and growing test counts are not proof of full TradingView 1:1 or all-36 completion.
