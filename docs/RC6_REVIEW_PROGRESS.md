# RC6 progress — 2026-09-06

## Stage 1 of the eight-stage plan: accepted architecture foundation

Tested OpenPine source: `ecf6f196f2845f7ccf5ca5e24e8abaa28d267560`.
Ast2Python: `25797f6484d3cbdbd47963db79fd235aae9d26d8`.
Joint verification and successful publication: `34062106441`.

[Stage 1 publication receipt](RC6_STAGE1_PUBLICATION.md) records observed acceptance
of five infrastructure criteria: immutable admission configuration, installed
capability graph and required-call checks, component ownership, a frozen manual
conformance corpus, and exact mandatory test inventories. The retained permanent
CI executes and aggregates these gates; it does not substitute a report for tests.

Both Python 3.11/3.13 ran **4,789 cases with no failures, errors or skips** in the
selected inventories. This count includes 37 existing accounting checks and new
infrastructure tests; it is not a TradingView conformance count. Six library suites
are complete, the provider excludes five live-network cases explicitly, and
OpenPine uses native plus selected regressions. Two new protected-worker tests,
152 Vitest cases, 22 Node cases, actual frontend build/API checks and host
wheel/sdist builds passed. The first run's new bulk-test representation error was
fixed without changing assertions, expected traces or the locked inventory.

Architecture and corpus reports match between interpreters. The corpus has 12/12
manual expected cases; tradingview_verified remains false. Each execution-mode
capability graph has 8,134 installed-catalog version decisions: 949 BOUND, 5,699
UNAVAILABLE and 1,486 UNVERIFIED, not a percentage of official Pine support.
Missing required runtime primitives can no longer inherit executable reference
fallbacks. Known legacy upstream config provenance is explicitly unresolved.

[Architecture contract](RC6_STAGE1_ARCHITECTURE.md) and `verification/stages.json`
preserve the complete original OP mapping and exact specification checksum.
The stage is accepted as a foundation, not full acceptance of OP-03/12/15/32/35.
The next principal stage is the complete agreed language block, not additional
isolated trading features. This progress/receipt commit changes documentation only;
new CI results after it are not assumed.

## Previous verified entry-risk integration

Tested OpenPine source: `17c65b7a5c0d1dd9dcb76105f7ba6a7428bb4b09`.
Engine: `f7d286c2309ef8f8679c37cf5ba730414c13e575`.
Pine2AST: `2a6f16d20d8d9beba49dcede62dc0c5607e9d92c`.

[Publication receipt](RC6_ENTRY_RISK_PUBLICATION.md) records five original commits,
actual joint run `34055095961`, source identities, archive digests and successful
publication retry. Independently parsed JUnit: 4,690 functional cases plus 37
accounting checks per Python 3.11/3.13. Historical counts overlap with Stage 1 and
must not be added. Existing risk, metadata, trailing and per-fill exit semantics
were not reimplemented by the architecture work.

## All 36 tasks and preserved work

[RC6_REVIEW_36.md](RC6_REVIEW_36.md) and [RC6_REVIEW_36.json](RC6_REVIEW_36.json)
retain the original specification IDs and headings. Whole-task statuses remain
29 partial, six unverified and one accepted (OpenPine branch consolidation).
`verification/stages.json` adds eight delivery stages without erasing unresolved
criteria or presenting local engineering fixtures as external TradingView evidence.

Seven important host request/transport/checkpoint/capability/marketdata/strategy-host/
optimizer files and the UI tree are byte-identical to the Stage 1 baseline. Only
the Ast2Python sibling pin changed. Main and historical releases are unchanged;
publication archived and removed its temporary branch. Use coordinated
[source pins](RC6_LIFECYCLE_SOURCES.json), not identical version strings.

## Remaining cross-stage acceptance

Full upstream configuration provenance, complete versioned language/API conformance,
data discovery and unsupported request contexts, full broker/IPC/worker restart,
remaining broker semantics, cache/performance measurements, browser UX, external
oracle coverage and immutable wheel-only delivery remain open. These are assigned
to the remaining seven stages. No full 36-task closure or speedup is claimed.
