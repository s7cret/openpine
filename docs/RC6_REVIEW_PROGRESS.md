# RC6 review progress — 2026-09-06

Implementation and full acceptance of the 36 tasks are tracked separately in
[RC6_REVIEW_36.md](RC6_REVIEW_36.md) and [RC6_REVIEW_36.json](RC6_REVIEW_36.json).
Original IDs, headings, specification checksum and whole-task statuses are retained.
Test counts are not percentages of TradingView compatibility.

## Latest verified price-entry bracket block

OpenPine tested source: `cc5f8d116660d40c6e42ffa1feef3cbcaada7d9e`.
Backtest Engine: `891141faf482c76ed1f85a7f4b0076f26ed63336`.
Joint CI/publication `34029925432` passed 3,789 functional cases and 37 accounting
checks per interpreter (Python 3.11/3.13), plus 152 Vitest and 22 Node cases.
Six new real protected worker scenarios passed. Both repository publications
completed on their first attempts; source refs and downloaded evidence were verified.

OP-07/20 now include explicit brackets on pending limit, stop and stop-limit entries,
nearest-forward price traversal, no retroactive low/high after activation, no close
callback path replay, OCA price-event order and chronological magnifier subbar opens.
Three obsolete negative assertions were upgraded, and 95 functional cases added.
The source follows the newest remote RC6 without replacing prior request/checkpoint,
configuration or optimizer work. Eight protected host runtime files are unchanged.

[RC6_PRICE_ENTRY_EXITS_PUBLICATION.md](RC6_PRICE_ENTRY_EXITS_PUBLICATION.md) records
actual source identities, counts, artifact digests, preservation and limitations.
[RC6_PRICE_ENTRY_EXITS_BLOCK.md](RC6_PRICE_ENTRY_EXITS_BLOCK.md) describes the implementation.
This progress/receipt/ledger commit changes documentation only after the tested source;
it does not assert an unobserved new full CI run for the later documentation revision.

## Preserved prior blocks

- [Market brackets and admitted ticks](RC6_EXIT_BRACKETS_PUBLICATION.md): retained
  sizing, reservation and actual instrument-tick behavior; its price-entry limitation
  is superseded only for the subset documented in the new receipt.
- [Optimizer integration](RC6_OPTIMIZER_PUBLICATION_RECEIPT.md): independent trials,
  validated results, warmup and full optimizer checks.
- [Nested requests](RC6_NESTED_MERGE_PUBLICATION.md) and
  [checkpoints](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md): preserved child contexts,
  chunked snapshots and receipt-checked state.
- [Data/UI](RC6_DATA_UI_PUBLICATION_RECEIPT.md),
  [strategy surface](RC6_STRATEGY_PUBLICATION_RECEIPT.md),
  [cleanup/progress](RC6_CLEANUP_EXECUTION_BLOCK.md),
  [lifecycle](RC6_LIFECYCLE_BLOCK.md) and [inputs](RC6_INPUTS_BLOCK.md).

## Remaining acceptance and deployment

Explicit from_entry is still required. All-entry/trailing/repeated-entry relative
levels, v6 mixed levels, leg metadata, risk/indexed trades, full broker/IPC recovery,
automatic request loading, immutable installation, broader realtime, browser UX and
an independent Pine v1-v6 oracle corpus remain open. No full OP-07/20 or 36-task
acceptance, complete TV equivalence or performance improvement is claimed.

Six library suites are complete; provider excludes five network cases and OpenPine
uses native plus selected regressions. Coverage and browser visual gates are separate.
Use [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json), update host/worker/engine
together and recompile for the changed host surface. Same version strings do not
identify the source. OpenPine keeps four target branches; main and historical releases
are untouched, maintenance refs archived as tags. No cleanup of all sibling branches
or standalone production installer is claimed.
