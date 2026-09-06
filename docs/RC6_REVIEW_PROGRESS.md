# RC6 progress — 2026-09-06

## Latest published entry-risk integration

Tested OpenPine source: `17c65b7a5c0d1dd9dcb76105f7ba6a7428bb4b09`.
Engine: `f7d286c2309ef8f8679c37cf5ba730414c13e575`.
Pine2AST: `2a6f16d20d8d9beba49dcede62dc0c5607e9d92c`.

[Publication receipt](RC6_ENTRY_RISK_PUBLICATION.md) records five original commits,
actual joint run `34055095961`, source identities, archive digests and successful
publication retry. Independently parsed JUnit: **4,690 functional cases plus 37
accounting checks per Python 3.11/3.13**, zero failures/errors/skips. Four new real
worker variants passed. Frontend: 152 Vitest and 22 Node tests, actual build/API
checks. Six library suites are full; provider excludes five live-network cases,
and OpenPine is native plus selected regressions, not the complete project inventory.

OP-07/20: two entry-risk commands now join the original seven trading commands.
Maximum position size clips actual entry exposure and is rechecked at fill, without
constraining strategy.order. Prohibited reversals close the whole position at market.
Tests cover pending orders, closing components, step/minimum/zero quantities, clipped
order stability, metadata and native policy changes. The compiled subset requires
unconditional global declarations and one direction declaration; other forms fail
explicitly instead of silently gaining different semantics.

OP-02/14: active-version namespaces and scalar/source input qualifiers are corrected,
including legacy input and negative series-valued risk cases across v1-v6.
OP-10: native broker/realtime snapshots retain validated risk state; full isolated
broker/IPC/worker restart remains open. [Implementation notes](RC6_ENTRY_RISK.md)
explain legacy snapshot compatibility and unsupported cases.

This receipt/progress update changes documentation only, not tested runtime, UI or
workflows. No separate completed CI run for a later documentation SHA is implied.

## All 36 tasks and preserved work

[RC6_REVIEW_36.md](RC6_REVIEW_36.md) and [RC6_REVIEW_36.json](RC6_REVIEW_36.json)
retain the original specification IDs and headings. Status describes whole-task
acceptance, not implementation percentage: 29 partial, six unverified, one accepted
(OpenPine branch consolidation only). The updated scope retains previous trailing,
per-leg metadata, absolute per-fill exits, requests, checkpoints and optimizer work.

Nine important host runtime files and the UI tree are byte-identical to the preceding
base. Main and historical releases are unchanged; the temporary publication branches
are archived as tags. OpenPine again has exactly four target branches. Use the exact
coordinated [source pins](RC6_LIFECYCLE_SOURCES.json) and recompile artifacts. Evidence
archives contain sources and tests, not a self-contained production installer.

## Remaining acceptance

Other risk functions, unrestricted declaration extraction, FIFO/ANY, fixed-stop versus
trailing arbitration, indexed trades, deferred alert expressions/delivery, automatic
request data discovery/UDF/live contexts, complete restart, immutable wheels/doctor,
winner replay/holdout, external Pine v1-v6 conformance and browser UX remain open.
No performance speedup, coverage-gate completion, full 36-task closure or TradingView
1:1 acceptance is claimed.
