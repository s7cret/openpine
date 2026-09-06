# RC6 review progress — 2026-09-06

This ledger distinguishes implementation from full acceptance of the original 36
tasks. Test counts are not percentages of TradingView compatibility.

## Current complete task inventory

[RC6_REVIEW_36.md](RC6_REVIEW_36.md) and [RC6_REVIEW_36.json](RC6_REVIEW_36.json)
preserve every original OP-01–OP-35 heading and the separately authorized OP-36
branch task, plus the original specification checksum. Every task records its
implemented scope, remaining acceptance and evidence paths. Snapshot status:
29 partial, six requiring full verification, and one accepted (OpenPine branch
consolidation only). These statuses do not discard prior implemented fixes.

## Latest verified optimizer / request integration

OpenPine tested head: `9328e45dc8d0aff8de1e4e7a0a9837afd785e2b8`.
Optimizer tested head: `95570459e50492dea8872b0a25f094e29d3e821f`.
Joint run `34005041354` passed 3,613 functional cases plus 37 ledger consistency
checks on each of Python 3.11 and 3.13: 3,650 total per interpreter, zero failures,
errors or skips in executed selections. Six full library suites now include the
optimizer and its unchanged process-containment suite; provider excludes five
live-network cases and OpenPine remains a native/affected-path selection.
Frontend has 152 passing Vitest cases, successful Node regressions, type/build/API
checks. Real serial/concurrent optimizer workers ran with the sandbox enabled.

- OP-26: trial warmup including zero, independent mutable inputs and explicit durable
  identities; verified request snapshots rebound to each trial without altering data;
  public host conversion retains the manifest. Numeric result fields are translated
  to actual broker computation controls rather than passed as unsupported switches.
- OP-27: failed/partial/error-bearing/nonfinite results cannot win ranking. Real zero
  metrics remain zero. Supported response contracts enforce required-output metadata.
  Repeated/reordered trials are compared with ordinary backtests under the same
  context, including intent tapes, trades, equity and score-ledger identity.
- OP-12: permanent read-only CI now includes all seven libraries, with the provider
  network exception above, plus native workers and frontend/API checks.

[Publication receipt](RC6_OPTIMIZER_PUBLICATION_RECEIPT.md) contains the seven exact
commits, observed results, artifact digests, retry history and limitations. This
progress/receipt update changes no tested runtime, UI or workflow code. New CI
results after documentation updates are not assumed.

## Preserved code and branches

Nested requests, checkpoint v2, canonical bars/underflow checks, request transport
and worker capability negotiation remain unchanged. Full source pins are in
[RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json); update them as a set rather
than trusting identical version strings. No independent production installer is
claimed. OpenPine keeps main, release/v2.17, release/v4.0.2 and release/5.0.0rc6;
main and historical heads are unchanged. Temporary publication branches are archived
as tags before deletion. Cleanup of all sibling repositories is not claimed.

## Earlier verified blocks (test counts overlap)

- [Nested requests](RC6_NESTED_MERGE_PUBLICATION.md): inherited nested contexts and
  chunked immutable request manifest transfer.
- [Requests and checkpoints](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md): source-context
  expressions, receipt-checked state and honest protocol capabilities.
- [Data and UI](RC6_DATA_UI_PUBLICATION_RECEIPT.md): strict imports, metadata,
  stale-response fixes and display-only sampling.
- [Strategy host](RC6_STRATEGY_PUBLICATION_RECEIPT.md): seven commands, 17 scalars;
  full exits/risk/indexed access remains partial.
- [Cleanup/progress](RC6_CLEANUP_EXECUTION_BLOCK.md), [lifecycle](RC6_LIFECYCLE_BLOCK.md)
  and [inputs](RC6_INPUTS_BLOCK.md): preserved previous work.

## Remaining acceptance

Complete exits/trailing/risk/indexed trades, automatic requested-series discovery,
unsupported UDF/live contexts, full-job restart, immutable wheels/doctor, locked
validation/holdout and user-facing winner replay, full version-specific signatures,
conformance corpus and browser UX remain open. No full OP-26/OP-27 or 36-task closure,
external TradingView 1:1 proof, speedup or final production release is claimed.
