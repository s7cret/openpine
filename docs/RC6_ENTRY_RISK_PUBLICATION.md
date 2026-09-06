# Entry-risk controls — verified publication, 2026-09-06

## Published source

Tested OpenPine head: `17c65b7a5c0d1dd9dcb76105f7ba6a7428bb4b09`.
Tree: `b129ed75bc6b2027644f853710401ba8a12bc523`.
Base: `b47ab3a3369252e89204f5b997c13b331ee45b6a`.

| Repository | Original commit | Implemented scope |
| --- | --- | --- |
| Pine2AST | `2a6f16d20d8d9beba49dcede62dc0c5607e9d92c` | OP-02/14: active-version namespaces, scalar/source input qualifiers, regenerated catalogs |
| Engine | `74ad1ee90e289d62e93bbf54b67b6746b25e582a` | OP-20/10: entry sizing, forbidden reversal, fill recheck and risk snapshots |
| Engine | `f7d286c2309ef8f8679c37cf5ba730414c13e575` | OP-07: two commands through existing RiskIntent and strict value validation |
| OpenPine | `b770989098577440481fa9eb44b8217ccf970159` | OP-07/20: host admission, published source pins and compiled/process regressions |
| OpenPine | `17c65b7a5c0d1dd9dcb76105f7ba6a7428bb4b09` | Permanent checks and updated all-36 scope ledger |

All five original commits are published with unchanged identities. This receipt and its progress update are documentation only after the tested source. No new runtime/UI behavior or separately observed repeat-CI result is implied by the documentation commit.

Joint CI/publication: https://github.com/s7cret/openpine/actions/runs/34055095961
Parser CI/publication: https://github.com/s7cret/pine2ast/actions/runs/34054037451
Engine CI/publication: https://github.com/s7cret/backtest_engine/actions/runs/34054612716

## Behavior and explicit boundaries

`strategy.risk.max_position_size` and `strategy.risk.allow_entry_in` use the existing RiskIntent 2.2.0 and engine-owned registry. They supplement seven trading commands and 17 scalar state values. No parallel broker, duplicate rule interpreter or new wire version is introduced.

The maximum limits resulting entry exposure, not total transaction size or nominal pending quantities. An oversized entry is clipped; capacity is checked again immediately before execution. Pending orders of 3 and 4 under a limit of 5 can fill as 3 and 2. Closing eight while reversing into three can require a transaction of eleven. A queued full close must not contribute its closing size twice. A clipped/OCA-reduced pending order never regrows when capacity later becomes available. Quantity steps round capacity down; insufficient minimum size cancels before fees, fills or position changes. These two rules do not constrain `strategy.order` or explicit exits/closes.

A prohibited opposite entry becomes a full market close, not a partial reduction or price-conditioned reversal. Comments and alert metadata survive that conversion. Native changes to direction rules reconcile already queued entries. This is tested native behavior, not a claim that Pine permits series-valued dynamic risk controls.

The compiled subset deliberately requires unconditional global declarations and fixed-for-run values, including inputs. Conditional/local/UDF declarations and multiple direction declarations are explicitly rejected until correct extraction/precedence is implemented. Other risk functions remain unbound. Multiple maximum-size rules use the strictest value. Zero is meaningful; invalid/missing/boolean/nonfinite limits and historical `when` are rejected. This restriction is not presented as full Pine risk syntax support.

Parser fixes derive intermediate namespaces only from the active catalog. Legacy scalar `input(...)` retains an input qualifier, while source inputs remain series and cannot pass as simple-only risk values. Tests cover v1-v6 positive/negative bindings; this is not a complete historical signature census or a live TradingView oracle.

Native resume and realtime broker snapshots retain risk flags and limits. New version-1 risk sections must be complete and valid before replacement. Rules registered only before a checkpoint survive continuation; fills, trades and equity match uninterrupted tests. Legacy version-0 snapshots without risk sections keep compatibility but cannot prove their original active rules. Checksums/versions are not authentication. Full isolated broker/IPC/worker restart and complete realtime parity remain open.

## Independently checked test results

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| Contracts | 557 | 557 |
| PineLib | 229 | 229 |
| Pine2AST | 413 | 413 |
| Ast2Python | 372 | 372 |
| Backtest Engine | 1102 | 1102 |
| Optimizer | 281 | 281 |
| MarketData Provider, non-network selection | 601 | 601 |
| OpenPine functional native/affected-path selection | 1135 | 1135 |
| Functional total | **4690** | **4690** |
| Review accounting checks | 37 | 37 |
| Total | **4727** | **4727** |

Both downloaded JUnit sets have zero failures, errors and skips. New cases: 78 broker + 17 intent + 42 parser + 45 host, plus two cases added by the expanded existing registry parametrization: 184 total. Previous incorrect rejection/partial-close assertions were corrected without removing unrelated checks. Four new real-worker variants passed in both interpreters: interactive/bulk with on-close off/on and fill recalculation enabled. Bubblewrap/AppArmor was retained. Repeated and standalone runs are not additional unique coverage.

Six library functional suites are complete; five provider live-network cases are explicitly excluded. OpenPine is native plus its selected affected paths, not every project test. The full parser includes its existing release/performance checks; the optimizer includes unchanged process-containment checks. Frontend evidence has 152 passing Vitest and 22 Node cases, real TypeScript/Vite production build and checks against OpenAPI from the same backend. Changed-source lint, compileall and wheel/sdist builds passed. Browser visuals, coverage thresholds, immutable wheel installation and independent TradingView execution remain separate acceptance.

## Integrity, publication and preservation

Archive digests were checked against GitHub, JUnit counted directly and the four new process tests located by name. Parser, Engine and both host bundles were restored independently; original patch hashes, raw commits and tree SHAs agree with the reviewed plans. The final publication bundle is byte-identical to the verified Python 3.13 bundle. Both joint source-pin files match RC6_LIFECYCLE_SOURCES.json.

| Artifact | SHA256 |
| --- | --- |
| Parser publication 9995427510 | `030ead4f34fb4bcda128577a0e9001e791399ebdf477994a8f2a25a7f5688b36` |
| Engine publication 9995581950 | `93da102b810c2a145a7d618a2a2313f1d76a15cca642449f6282669a3897ab37` |
| Joint 3.11 9995871624 | `c9c771e59a8f45b71a5edf79ea09c978d72a4f813605336e6de159981665da39` |
| Joint 3.13 9995862192 | `01cfacc7fc10b2a580eec365a85b114d795fec2593faebc19ea11bb3dafc65f2` |
| Frontend 9995880729 | `8ae60ba36df1623956ee3b4816e4930c56f9bc7630d425b7c26472fa57e439be` |
| Successful publication retry 9995912985 | `35e66e4033a2945b51ef4eaa370ecb80aa6131c024860f8df9bc7c9d8bf55b79` |

All joint tests and builds passed before publication. Actions then refused its workflow-file update because its token lacked workflows permission. After checking the unchanged remote base and exact tested tree, the connected GitHub API advanced RC6 with force=false. The publication retry succeeded and archived/deleted the temporary branch. No permissions or assertions were weakened.

The same-name tags `ops/rc6-entry-risk-20260906` preserve maintenance tips: Parser `66d107310fcd9a8868667906f47de5fb5737ee93`, Engine `42aa925d3d04cc8889905e1740da2859f2ab5c29`, OpenPine `85611ef7a930decc23720ec14c2147076fafb2be`. Publication receipts confirm exactly four OpenPine branches and unchanged main/historical heads. No unrelated sibling branches were deleted. One-shot publication helpers remain only in archived maintenance history.

Nine important host files for requests, transport, generated checkpoints, worker runtime/capabilities, canonical bars, configuration, isolated execution and optimizer, plus the UI tree, match the previous base byte for byte. Exact identities are in the delivery evidence. The complete all-36 inventory remains 29 partial, six unverified, one accepted (OpenPine branch consolidation). Previous trailing, metadata and absolute per-fill implementations are preserved in its updated scope descriptions.

## Remaining work and delivery

Other risk functions, arbitrary risk-declaration extraction, multiple direction-rule conformance, FIFO/ANY, fixed-stop/trailing arbitration, indexed trades, deferred alert expressions/delivery, all market quantity rules and independent version-specific oracle coverage remain open. Existing native drawdown settings being retained in snapshots do not certify Pine drawdown parity. No whole-backtest speedup or full 36-task completion is claimed.

Update the exact source set together and recompile generated artifacts against the changed host surface. Package version strings do not identify identical code. The evidence package contains full sources for all eight components, patches and Git history; it is not an autonomous production installer. See RC6_ENTRY_RISK.md for the supported API and snapshot migration details.

Semantic references (design sources, not execution evidence):
- https://www.tradingview.com/pine-script-docs/concepts/strategies/#risk-management
- https://www.tradingview.com/pine-script-docs/v4/essential/strategies/#risk-management
