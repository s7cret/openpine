# OP-07 / OP-20 — all-entry exit publication receipt

Date: 2026-09-06.

## Published and verified source

OpenPine baseline: `84657611816c540d9f447364f320134aa5f016e9`.
Tested source: `b36b9ac6c74a1c84348e91d376dc16e9f4d33e3f`.
Tested tree: `d60c901b7ce24c82682aa9d5c4bd2d820eed1d62`.
Contracts: `7c20559e87b3f2342adb0b849d4239c133567f0a`.
Backtest Engine: `884c7823d29344dbf847a0cf6c513d802b7c9ece`.

The exact five source/test/CI commits are preserved separately:

| Repository | Commit | Scope |
| --- | --- | --- |
| Contracts | `7c20559e87b3f2342adb0b849d4239c133567f0a` | Versioned all-entry intent and generator/contract regressions |
| Engine | `884c7823d29344dbf847a0cf6c513d802b7c9ece` | Position lifetime, per-fill targeting, reservations and native state |
| OpenPine | `4bc95140a095f985fd581b2aa4d04f1d5babdd1c` | Both transports, source pins, host admission and compiled-Pine tests |
| OpenPine | `8b933622eacdd33b78fe1f45b413c0d736a54b78` | Permanent read-only checks and scope documentation |
| OpenPine | `b36b9ac6c74a1c84348e91d376dc16e9f4d33e3f` | Exact frontend diagnostic plus independent host rejection test |

This receipt, progress update and the OP-07/20 ledger refresh are documentation only after the tested source. They do not change runtime, tests, UI or workflows. A subsequent full CI result on that documentation commit is not assumed.

## Functional result

Omitted or empty from_entry is an explicit all-entry exit. It attaches to current open or already-submitted pending entries, then protects subsequent same-direction entries for the lifetime of that position. Flat, reversal, cancel, cancel_all and replacement end or replace the corresponding policy. A flat call with no pending entry does not subscribe an unrelated future position.

All-entry exits and named relative profit/loss exits are materialized per actual opening fill, including repeated entry IDs on the same bar. Levels use the actual fill price and admitted mintick; partial quantities and reservations distinguish lots. Completed partial exits do not rearm old residual lots merely because another entry reuses their ID. Replacement removes obsolete targets and price legs. Ordinary opposite orders can reduce a reserved position without producing phantom non-reducing fills.

The wire extension is explicit: all-entry exits use intent version 2.3.0 and exit_scope=all_entries without from_entry. Named exits retain 2.2.0 and require from_entry. Other intent kinds do not gain 2.3.0 semantics. Literal entry names *, A:B and all_entries remain ordinary names. Neither missing fields in malformed old records nor ID spellings imply wildcard scope.

The broker, not the adapter, owns this behavior. Policies and fill identities are retained in native broker resume/realtime snapshots, copied independently, and cleared by warmup reset. Native long/short split/resume tests compare exact fills, closed trades and equity with uninterrupted execution, including a public exit ID containing a colon. This is not isolated worker/IPC restart recovery.

Representative synthetic case: fills of 2 units at 100 and 6 units at 110, profit=20 with mintick=1, qty_percent=50 produce relative exits of 1 at 120 and 3 at 130, leaving 1 and 3 units. Both named-relative and unqualified forms are tested. This is an explicit regression expectation, not a captured TradingView result.

## Actual checks

Joint verification/publication: https://github.com/s7cret/openpine/actions/runs/34034108879
Contracts verification/publication: https://github.com/s7cret/openpine-contracts/actions/runs/34032591698
Engine verification/publication: https://github.com/s7cret/backtest_engine/actions/runs/34032963817

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| Contracts | 395 | 395 |
| PineLib | 229 | 229 |
| Pine2AST | 338 | 338 |
| Ast2Python | 372 | 372 |
| Backtest Engine | 724 | 724 |
| Optimizer | 281 | 281 |
| MarketData Provider, deterministic non-network selection | 601 | 601 |
| OpenPine native / affected-path functional cases | 942 | 942 |
| Functional total | **3882** | **3882** |
| Ledger consistency, separately | 37 | 37 |
| Overall total | **3919** | **3919** |

All executed selections have zero failures, errors and skipped cases. The same cases run on two interpreters; standalone, local and prior runs do not increase the unique total. There are 93 new functional cases: 11 Contracts, 44 Engine and 38 OpenPine. Six library functional suites are complete. Five provider live-network tests were explicitly deselected; OpenPine uses native plus the existing explicit regression selection, not the full project inventory.

Four new tests ran real protected worker processes, interactive/bulk with process_orders_on_close off/on and fill recalculation enabled. Both interpreters passed all four. Bubblewrap/AppArmor were retained. Frontend: 152 Vitest and 22 Node cases, actual TypeScript/Vite production build and API checks against OpenAPI exported by this same tested backend. Changed-source Ruff, compilation and wheel/sdist builds passed. These are not browser visual/E2E tests or independent TradingView execution-oracle evidence.

The initial joint run 34033531671 correctly blocked publication. Its downloaded XML identified exactly one failing expectation: strategy.exit without any price leg is rejected by Pine2AST before reaching host admission. The test-only correction asserts P2A1404 and its source line, and adds an independent host preflight test with the generated price leg removed. No runtime behavior was weakened or changed to make that expectation pass. The initial run's four new protected-worker cases already passed. A local broad test invocation timed out and is not counted as a successful full run; the completed joint run above is the acceptance evidence for this scope.

## Verified artifacts and source preservation

Archive SHA256 values were checked against GitHub metadata. All JUnit files were parsed. Both host bundles were restored into independent checkouts at the explicit review-candidate head and their trees/pins compared. Contract and engine raw commit headers, patch digests and bundles match the original local commits.

| Artifact | SHA256 |
| --- | --- |
| Contracts publication, 9989110056 | `dc1c4eedc7fd3d83edcda16c3d49039900376a2751e847489f6a0ad22d6417b8` |
| Engine publication, 9989228942 | `b33907d4eb29da075f1418860fc62133bda60f6fbee8c26fe0a455f52c3237a6` |
| Host Python 3.11, 9989707153 | `941000630956d2dc2e07d92b858e0c54f245b96d3ad6ca6aa28ec60975e513ff` |
| Host Python 3.13, 9989704495 | `fc87232ed758952276c818d4871c4898e3ab0ee8154946ef16b45cc778fea80d` |
| Frontend, 9989715104 | `798fd5d00b6c409ebe1cb333c3bf5ca402d5af603648dba4bb8fedeb028bee81` |
| Final host publication, 9989719100 | `615fc7113c764cef548283fde30493ebe54b004b2c746cf853e160084daf72f0` |

Nine host runtime files for requests, transport, canonical bars, checkpoints, worker capabilities, configuration and optimizer retain identical Git blobs from the baseline. The entire UI source tree is unchanged. The release history is based on the fresh 8465761 baseline, not the older local checkout. The evidence contains exact preserved-file identities.

Publication jobs advanced each RC6 by ordinary fast-forward only after verification. Temporary ops/rc6-all-exits-20260906 branches were archived as same-name tags and removed atomically with expected-ref leases. Preserved tips are d4775540f22bac26d7ba607187c633e0c4159f69 (Contracts), 39fde991714776f1b4cb4b07fa831b9bae12900e (Engine), and ef8de754cf447b3b42202fb77d82eb3fc1ab8011 (OpenPine). No permission change or force update of a release branch was used. Before/after receipts and a fresh host read confirm exactly four OpenPine branches with unchanged main/v2.17/v4.0.2 heads. Cleanup of other preexisting sibling branches is not claimed.

The updated all-36 ledger retains all IDs, titles and acceptance statuses. Only OP-07/20 implementation, remaining criteria and evidence links change; 34 other machine records are unchanged. The documentation-only ledger refresh passed its 37 consistency tests locally against the verified source. Those tests are accounting checks, not TradingView conformance.

## Remaining acceptance and upgrade

Named absolute exits retain the prior aggregate quantity behavior. Complete FIFO/ANY allocation and trade-report attribution, including repeated-ID interactions, remain open. Per-fill target prices and the tested examples do not certify every default TradingView closing rule. Trailing, v6 mixed price pairs, per-leg metadata, risk, indexed methods, complete margin/commission/realtime interactions and external oracle coverage remain separate work. OP-07/20 and all 36 tasks are not fully accepted.

More per-fill brackets may increase order count and scan work. No timing or memory improvement was measured. Native state tests do not imply complete isolated job recovery.

Update host, worker, Engine and Contracts as the pinned set in RC6_LIFECYCLE_SOURCES.json and recompile artifacts for the changed host surface. Same package version strings are not sufficient. Old readers cannot process the new 2.3.0 all-entry intent. This evidence delivery is not an immutable standalone production installer.

Official semantics consulted: https://www.tradingview.com/pine-script-docs/concepts/strategies/
