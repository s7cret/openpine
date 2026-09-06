# RC6 review progress — 2026-09-06

The original 36 tasks remain in [RC6_REVIEW_36.md](RC6_REVIEW_36.md) and [RC6_REVIEW_36.json](RC6_REVIEW_36.json). Status is full-scope acceptance, not a percentage of code or TradingView compatibility. The snapshot still contains 29 partial tasks, six requiring verification and one accepted task (OpenPine branch consolidation only).

## Latest verified all-entry / relative-per-fill exit block

Tested OpenPine source: `b36b9ac6c74a1c84348e91d376dc16e9f4d33e3f`.
Contracts: `7c20559e87b3f2342adb0b849d4239c133567f0a`.
Backtest Engine: `884c7823d29344dbf847a0cf6c513d802b7c9ece`.

Joint run `34034108879` passed 3,882 functional cases plus 37 ledger checks on each Python 3.11/3.13, with zero failures/errors/skips in executed selections. Four new actual worker cases ran with the sandbox enabled. Frontend has 152 passing Vitest and 22 Node cases with production build/API checks. Six library suites are complete; provider excludes five live-network tests and host uses native plus selected regressions. Whole-project coverage and external TradingView execution acceptance are not claimed.

OP-07/20: omitted/empty from_entry now uses an explicit all_entries intent (2.3.0), while named exits remain 2.2.0. The broker persists all-entry protection through later same-direction entries until flat/reversal/cancellation. All-entry and relative exits use opening-fill identities for price, quantity and reserves. Replacement removes obsolete legs, native resume preserves the policy, and warmup reset clears it.

[Publication receipt](RC6_ALL_ENTRY_EXITS_PUBLICATION.md) records source commits, actual results, artifact digests, the corrected frontend-vs-host negative test and branch preservation. [Implementation notes](RC6_ALL_ENTRY_EXITS.md) describe the boundaries. This progress/ledger/receipt commit changes documentation only after the verified source; no unobserved full rerun is claimed.

Named absolute repeated-entry quantities, full FIFO/ANY matching/report attribution, trailing, v6 mixed levels, per-leg metadata, risk and indexed trades remain open. Full isolated broker/IPC/worker recovery is not implemented by native resume tests. No performance improvement is claimed; per-fill exit orders can increase work.

## Preserved earlier work

- [Price-entry exits](RC6_PRICE_ENTRY_EXITS_BLOCK.md): forward-only market/limit/stop/stop-limit execution, price-order OCA and chronological Bar Magnifier. Earlier notes requiring from_entry or leaving all-entry/relative repeat levels wholly unimplemented are superseded by this new scoped block.
- [Entry brackets](RC6_EXIT_BRACKETS_BLOCK.md): deferred market protection, admitted mintick and quantity/reservation fixes.
- [Optimizer publication](RC6_OPTIMIZER_PUBLICATION_RECEIPT.md): trial isolation, request rebinding, metric controls, strict ranking and full optimizer CI.
- [Nested requests](RC6_NESTED_MERGE_PUBLICATION.md), [requests/checkpoints](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md), and [data/UI](RC6_DATA_UI_PUBLICATION_RECEIPT.md): preserved source-context expressions, checkpoint v2, chunked transport and UI correctness.

Nine important host runtime files and the UI tree are unchanged from the preceding source. OpenPine still has main, release/v2.17, release/v4.0.2 and release/5.0.0rc6; historical heads are not rewritten. Temporary publication branches are preserved as tags before removal. Exact dependency pins are in [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json). Update the stack together and recompile artifacts; this is not a standalone production installer.

## Remaining major work

Complete broker/strategy semantics, request discovery/UDF/live contexts, full-job restart, immutable delivery/doctor, winner replay and validation, full version-specific catalog/conformance corpus, browser UX and all-36 acceptance remain open. Existing passing cases and explicit unsupported boundaries do not replace independent TradingView oracle evidence.
