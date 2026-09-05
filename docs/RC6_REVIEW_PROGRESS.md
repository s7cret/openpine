# RC6 review progress — 2026-09-05

This is a partial implementation ledger, not acceptance of all 36 review tasks or TradingView 1:1 compatibility.

## Latest verified runtime block

OpenPine runtime head: `ff718ec6ef5b78732abd463b0c4c64e2c8560e3d`.
Integrated cleanup/progress CI `33982457446`: **2,048 tests passed on Python 3.11 and 2,048 on Python 3.13**, with zero failures, errors or skips. Changed-runtime lint, wheel and sdist builds passed. These are four complete library functional suites plus native/sandbox and selected OpenPine regressions, not every test of all seven libraries.

See [RC6_CLEANUP_EXECUTION_BLOCK.md](RC6_CLEANUP_EXECUTION_BLOCK.md) for the six exact source commits, cleanup boundaries, sealed chunk transport, progress, actual counts and CLI input fixes. The transport serialization microbenchmark is explicitly scoped and records its memory tradeoff. The worker policy now explicitly requires msgpack; stale admissions must be rebuilt/re-admitted, not bypassed.

Previous lifecycle runtime `295a6885f1094676ae1bfdc90631814daa9e8966` passed 1,822 cases per interpreter; see [RC6_LIFECYCLE_BLOCK.md](RC6_LIFECYCLE_BLOCK.md). Historical counts overlap and must not be added. The sibling revisions remain [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json); no sibling repository was modified in the cleanup pass.

## OP-36 branch preservation

[RC6_BRANCH_SELECTION.json](RC6_BRANCH_SELECTION.json) maps 12 source commits to their existing preservation commits and records four intentionally excluded RC5 dependency-only commits. The tested retirement procedure archives original branch tips as tags, verifies source preservation and refuses changed refs. The actual final inventory is recorded by the separate consolidation Actions receipt. Main and historical release heads are not promoted to RC6.

## Previous publication records

- [Inputs and optimizer trials](RC6_INPUTS_BLOCK.md): previous 340/189/346 passing suites and source pins.
- [First publication](RC6_REVIEW_PROGRESS_FIRST_PUBLICATION.md): previous 236-test suite and historical limitations.

Earlier claims that inputs were unimplemented or zero margin was rejected are superseded for the tested paths by these later blocks. Earlier test counts are historical and must not be added to current counts.

## Remaining acceptance

Immutable production deployment, complete request integration, full strategy namespace, genuine checkpoints and state-hash equivalence, comprehensive realtime, UI and full Pine v1-v6 TradingView oracle coverage remain open. Complete configuration provenance and advanced optimizer request/parallel acceptance are not closed. Only input-frame serialization was benchmarked; no whole-backtest speedup or final production release is claimed.
