# Qualifier boundary — verified and integrated on 2026-09-11

PR #15 merged normally as `cf45adb47f495194729ec0bd7508caab8e751147`.
Original source `4d83645ea6373e74530e13c4cdcc48779803c836` and all original
library commits are preserved. Tree `ec4e5d2f3a0d74bc55047d99e91281379b9ee3c6`
matches the tested PR merge and both downloaded source archives.

Joint run: https://github.com/s7cret/openpine/actions/runs/34627696246

Both Python 3.11 and 3.13 executed 20,423 cases: 1,275 Ast2Python, 1,102 Engine,
601 Provider, 557 Contracts, 8,790 OpenPine, 281 Optimizer, 2,910 Pine2AST and
4,907 PineLib. Downloaded JUnit and exact inventories have no failures/errors/skips.
Six library suites are complete; Provider excludes five live-network cases;
OpenPine is native plus its fixed affected-path selection, not the entire project.
Counts include accounting/infrastructure checks, not only Pine conformance.

Protected workers, the unchanged 12-case manual corpus, architecture/capability,
Ruff/build/API gates and frontend passed. Frontend XML contains 152 Vitest cases;
the Node packaging/API test job passed. External TradingView execution, browser
visual acceptance, full coverage thresholds and full Stage 2 are not claimed.
Archive hashes and counts are in `verification/qualifier-joint-receipt-20260911.json`.

PineLib RC6 advanced without force to `075c59e451bff68790582028b1b0c8f3b78db267`;
Ast2Python to `c91e94396e5501812284dcf2478f18971f839c77` before the host merge.
Main and historical releases were not updated. Old upload jobs proved only source
preservation; the functional evidence is the joint run above.

This receipt supersedes older local-only status for the qualifier block. The new
library-method candidate has additional source changes and requires its own joint
run. The qualifier pass cannot stand in for testing those changes.
