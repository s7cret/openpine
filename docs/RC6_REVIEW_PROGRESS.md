# RC6 review progress — 2026-09-06

The complete original task inventory remains in [RC6_REVIEW_36.md](RC6_REVIEW_36.md) and [RC6_REVIEW_36.json](RC6_REVIEW_36.json). Status means full-scope acceptance, not a code or TradingView compatibility percentage. There are still 29 partial tasks, six requiring verification and one accepted task (OpenPine branch consolidation only).

## Latest verified mixed fixed-price exits

OpenPine tested source: `4ec4d177bf0d0dbc0ffc2891f79332ac2bb6785c`.
Contracts: `db372bc7b135a084667d3c726eca0465d21f008b`.
Engine: `3e5271671afdd41d9cba7400ac70fc569edeb6ad`.

Joint publication run `34037631453` and permanent repeat `34038152756` succeeded. Independently parsed XML confirms 4,007 functional cases plus 37 accounting checks per Python 3.11/3.13, zero failures/errors/skips in executed selections. Four new actual protected-worker scenarios ran. Frontend: 152 Vitest and 22 Node cases, actual build/API checks. Six library suites are complete; provider excludes five network cases; host uses native plus selected regressions. No external TradingView execution, coverage or visual acceptance is inferred.

OP-07/20: v6 fixed profit+limit and loss+stop pairs select the first-triggering level separately for each actual entry fill. V1-v5 and historical replay formats retain absolute precedence. A versioned 2.4.0 intent carries the explicit policy and unambiguous named/all-entry scope. Existing entry activation, OCA/reservations, cancellation, native resume and real zeros are preserved. NA is not replaced with a zero distance.

[Publication receipt](RC6_MIXED_EXIT_PUBLICATION.md) records the four original commits, full test accounting, exact artifacts and preserved branch/source identities. [Implementation notes](RC6_MIXED_EXIT_PRICES.md) describe the contract. The net increase is 125 functional cases: 127 newly added cases replace two obsolete unsupported-behavior assertions. This documentation/ledger update changes no tested runtime, UI or CI; a later documentation commit has no new full CI result unless separately observed.

Remaining within OP-07/20: trailing and activation pairs, per-leg metadata, FIFO/ANY attribution, named absolute repeated-entry quantities, risk/indexed methods and the full version/margin/realtime matrix. The fixed mixed-price gap is no longer listed as wholly unimplemented. Whole-task statuses are unchanged.

## Preserved earlier work

- [All-entry exits](RC6_ALL_ENTRY_EXITS_PUBLICATION.md): versioned all-entry scope, position lifetime and per-fill relative levels. Its mixed-fixed-price limitation is superseded by this block; other limits remain.
- [Price-entry exits](RC6_PRICE_ENTRY_EXITS_BLOCK.md): chronological market/limit/stop/stop-limit execution, price-order OCA and Bar Magnifier.
- [Entry brackets](RC6_EXIT_BRACKETS_BLOCK.md): deferred protection, admitted mintick and reservation fixes.
- [Optimizer](RC6_OPTIMIZER_PUBLICATION_RECEIPT.md): independent trials, request rebinding, metric controls, strict ranking and full optimizer CI.
- [Nested requests](RC6_NESTED_MERGE_PUBLICATION.md), [checkpoints](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md) and [data/UI](RC6_DATA_UI_PUBLICATION_RECEIPT.md): preserved source-context execution, checkpoint v2, chunked transport and UI correctness.

Nine important host runtime files and the UI tree are unchanged from the prior all-entry source. The verified branch receipt keeps main, release/v2.17, release/v4.0.2 and release/5.0.0rc6; historical heads are unchanged. Temporary publication refs are preserved as tags. Exact pins are in [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json). Update host/worker/Contracts/Engine together and recompile artifacts; old readers cannot consume new 2.4.0 mixed exits.

## Remaining major work

Complete strategy/broker semantics, requested-series discovery/UDF/live contexts, full-job restart, immutable delivery/doctor, winner replay/holdout, full version-specific conformance and browser UX remain open. No whole-backtest speedup or autonomous production installer is claimed. Passing regressions and explicit unsupported boundaries are not complete TradingView 1:1 acceptance.
