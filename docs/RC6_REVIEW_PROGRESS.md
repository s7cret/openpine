# RC6 review progress — 2026-09-06

The original all-36 inventory is preserved in RC6_REVIEW_36.json and RC6_REVIEW_36.md with its named historical snapshot. Newer publication receipts supersede only their explicit implemented scopes. Whole-task status is not a code or TradingView percentage: 29 partial, six unverified and one accepted (OpenPine branch consolidation only).

## Latest verified metadata transfer and absolute exit lots

Tested host: `0509399dfa4f4478ae1815ed0a862e40dca9867c`.
Contracts: `5950e0f99214e60b64162d074ed47f1e4bbc7141`.
Engine: `d5d9c8e6cd1cad40dd5402bcf4521519a1de522e`.
Pine2AST: `a0e548365eba137c247758a3da53c5514398043d`.

Joint run `34050729011` verified **4,506 functional cases plus 37 accounting checks on each of Python 3.11 and 3.13**, with zero failures/errors/skips in the executed selections. All eight new protected-worker variants passed. Frontend: 152 Vitest and 22 Node cases, actual TypeScript/Vite build and same-backend API checks. The full parser suite, including release/performance tests, and the full optimizer process-containment suite ran in CI. Six library functional suites are complete; provider excludes five live-network cases; host uses native plus explicit affected-path regressions.

[RC6_METADATA_TRANSFER_PUBLICATION.md](RC6_METADATA_TRANSFER_PUBLICATION.md) records source selection, original and replacement commits, digest-checked evidence and the completed publication retry. It supersedes the previous local-only status. The archived nested 2.6 wrapper was not copied over the independently published flat 2.6 schema. The parser commit retains its original SHA; compatible host/engine changes are explicitly ported without overwriting newer per-fill quantity or metadata models.

- OP-07/14/30: comments and alert flags survive replay and executed-leg selection; fill events retain snapshots; historical trade text is not reconstructed from later same-ID orders. Positional exit binding uses the versioned catalog and no phantom oca_type slot. NA-inactive parameters do not create false trailing conflicts.
- OP-20: the published per-opening-fill absolute exit quantities are integrated and tested across long/short, TP/SL/bracket, named/all scope, quantity precedence and reserve amendments. The complete host matrix includes real workers. Full FIFO/ANY attribution is still open.
- OP-06/30: a real bulk omission found by the process gate is fixed. available_outputs now comes from the broker's actual result, preserving the distinction between uncollected data and collected-empty outputs. The initial six new process failures and corrected fixture assumptions remain documented, not hidden by skips or weakened assertions.

The coordinated snapshot adds 391 functional cases over the previous trailing baseline, including reused new library work. This is not 391 newly authored tests in this transfer. New host cases are 29 transferred metadata, 48 absolute-lot and four bulk-availability cases. Repeated/overlapping runs are not summed as unique coverage.

## Publication and preserved work

All source verification preceded release publication. The Actions token lacked workflow-write permission; after rechecking the expected base and tested tree, the connected API advanced RC6 with force=false. The successful publication retry verified the head and archived/deleted the temporary branch. Four OpenPine branches remain, with main and historical releases unchanged. No account permission settings were modified.

Eight important host runtime files and the UI tree remain byte-identical to the previous base. The worker runtime changes only by the two-line actual-output-availability export; requests, checkpoint v2, transport, canonical bars, config and optimizer implementation are retained. Exact source pins are in [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json).

This progress/receipt update changes documentation only after the tested source. It does not establish a new full CI result for the later documentation SHA. The delivery includes the verified source snapshots of all eight components, original-to-selected mapping, individual patches and test evidence; it is not an autonomous immutable production installer. Recompile generated artifacts and never mix the obsolete archive's private 2.6 wrapper with the current published format.

## Remaining acceptance

Alert strings are captured when commands execute, not arbitrarily reevaluated on a later order fill. External delivery, placeholders and full restart deduplication remain open. Captured eligibility is not a delivery confirmation.

Fixed-stop/trailing competition, FIFO/ANY, risk/indexed access, complete historical signatures, automatic requested-series discovery/UDF/live contexts, complete broker/IPC/worker restart, immutable delivery/doctor, winning-trial replay/holdout, independent conformance and browser UX remain open. No complete OP-06/07/14/20/30 or all-36 acceptance, external TradingView 1:1 proof, or measured speedup is claimed.

## Earlier verified blocks

[Trailing](RC6_TRAILING_PUBLICATION.md), [mixed fixed prices](RC6_MIXED_EXIT_PUBLICATION.md), [all-entry exits](RC6_ALL_ENTRY_EXITS_PUBLICATION.md), [price entries](RC6_PRICE_ENTRY_EXITS_BLOCK.md), [optimizer](RC6_OPTIMIZER_PUBLICATION_RECEIPT.md), [nested requests](RC6_NESTED_MERGE_PUBLICATION.md), [checkpoints](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md) and [data/UI](RC6_DATA_UI_PUBLICATION_RECEIPT.md) remain preserved. Their implemented scopes are not discarded; limitations explicitly resolved by the newest receipt are superseded.
