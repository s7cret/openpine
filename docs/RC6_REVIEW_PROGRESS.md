# RC6 review progress — 2026-09-05

This is a partial implementation ledger, not acceptance of all 36 review tasks or TradingView 1:1 compatibility.

## Latest verified runtime block

OpenPine code head: `295a6885f1094676ae1bfdc90631814daa9e8966`.
Integrated CI `33977499788`: **1,822 tests passed on Python 3.11 and 1,822 on Python 3.13**, with no failures, errors or skipped cases. Both wheel/sdist builds passed. This is four complete library functional suites plus selected OpenPine/native/sandbox regressions, not every test of all seven libraries.

See [RC6_LIFECYCLE_BLOCK.md](RC6_LIFECYCLE_BLOCK.md) for OP-03 rounding/zero margins, OP-05 causal lifecycle/recalculation, OP-06 verified completion, exact commits and evidence. The compatible sibling revisions are [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json). Update the source set together rather than mixing stale same-version wheels.

## OP-36 branch preservation

[RC6_BRANCH_SELECTION.json](RC6_BRANCH_SELECTION.json) maps 12 source commits to their existing preservation commits and records four intentionally excluded RC5 dependency-only commits. The tested retirement procedure archives original branch tips as tags, verifies source preservation and refuses changed refs. The actual final inventory is recorded by the separate consolidation Actions receipt. Main and historical release heads are not promoted to RC6.

## Previous publication records

- [Inputs and optimizer trials](RC6_INPUTS_BLOCK.md): previous 340/189/346 passing suites and source pins.
- [First publication](RC6_REVIEW_PROGRESS_FIRST_PUBLICATION.md): previous 236-test suite and historical limitations.

Earlier claims that inputs were unimplemented or zero margin was rejected are superseded for the tested paths by these later blocks. Earlier test counts are historical and must not be added to current counts.

## Remaining acceptance

Immutable production deployment, complete request integration, full strategy namespace, genuine checkpoints and state-hash equivalence, comprehensive realtime, UI and full Pine v1-v6 TradingView oracle coverage remain open. Complete configuration provenance and advanced optimizer request/parallel acceptance are not closed. No measured performance improvement or final production release is claimed.
