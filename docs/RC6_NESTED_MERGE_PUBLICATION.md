# OP-08 — nested request reconciliation: published 2026-09-06

## Verified source and preserved history

OpenPine source head: `17430a5638d299245b19d30c0d6f5a0166af6ff0`.
Tree: `a76cc5238caa7e13b14e2e847e44e157506383de`.
Base: `220285905ac994564c3a70d0af264dce3bb9f288`.
Joint CI/publication: https://github.com/s7cret/openpine/actions/runs/34001163455

The older local OpenPine candidate was not written over the current release. Useful nested-request library commits were preserved exactly, while OpenPine integration was reconciled with the published request_manifest API and newer safeguards. The generated checkpoint v2 implementation, worker capability negotiation, canonical numeric-underflow decoder and their three regression files have identical Git blobs before and after this merge. Source pins include all seven compatible libraries.

| Repository | Published commit | Scope |
| --- | --- | --- |
| PineLib | `c187e7c531938f71b36587bcd917ff8f8bca4ce2` | Nested calls share outer RequestEngine budgets, cache, identities and rollback; explicit source aliases |
| PineLib | `cddaa0bf2324db36b4cd29e944a251032658c59d` | Test import normalization only |
| Ast2Python | `51552e7c1de339d8de4794290655e574d6447e82` | Nested expression methods under dynamic-request policy |
| OpenPine | `367b4651b680b272c0d88780f5818de9e6d476cb` | Context-aware nested preflight and runtime policy; exact sibling pins |
| OpenPine | `4dbbd9150c206cfdae09d95719cc4b88f399214e` | Bounded, verified transport of the existing request manifest |
| OpenPine | `fe4480a238b253eeebc54fe154e2b262ace27a3e` | Actual multi-frame worker tests using 200 canonical source bars |
| OpenPine | `17430a5638d299245b19d30c0d6f5a0166af6ff0` | Retained read-only CI and implementation notes |

The original three library commit SHAs match the supplied local bundles. OpenPine's four task commits are new reconciled changes, not a claim that the old OpenPine history was merged wholesale. This publication receipt changes documentation only.

## Implemented behavior

Empty symbol/timeframe in nested requests inherits the enclosing requested context. Preflight follows child methods with that context; lower-timeframe validation compares with the immediate parent, not the chart. Dynamic unresolved parameters remain runtime decisions. Missing static sources still fail before worker staging. Tests cover actual nested SMA/broker orders, inherited lower timeframes, future-only mutations, depth-limit rollback and exact checkpoint continuation with cached child datasets and published callback receipts.

The host API remains `config.request_manifest = build_request_manifest(execution_context, datasets)`. No competing request_sources API is installed. Bootstrap now contains a descriptor followed by numbered, hashed chunks. The worker reconstructs and revalidates the same manifest and effective configuration before HELLO or generated execution. Truncation, reordering, duplicate keys, changed sizes/hashes, foreign context and altered config are rejected without partially replacing the configuration.

Limits: 128 KiB raw chunks, 256 KiB encoded frames, 2 MiB spool rollover, 64 MiB encoded preload cap, at most 64 datasets and 250,000 source bars in aggregate. These are transport safeguards, not TradingView limits or a promise that every worker can run the maximum preload. Existing memory/private temporary-filesystem limits can bind sooner; decoded source rows, caches and broker results still materialize. No full cancellation/backpressure or measured speedup is claimed.

## Verification evidence

| Joint functional suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 384 | 384 |
| PineLib | 229 | 229 |
| Pine2AST | 338 | 338 |
| Ast2Python | 372 | 372 |
| Backtest Engine | 586 | 586 |
| MarketData Provider, deterministic selection | 601 | 601 |
| OpenPine native and explicit affected paths | 805 | 805 |
| Total | **3315** | **3315** |

Downloaded XML reports contain zero failures, errors or skips. The same 3,315 cases run on two interpreters; historical runs and separate library checks must not be added as unique cases. Five provider live-network cases were explicitly excluded. The first five library functional suites are complete; OpenPine is selected, and the full standalone optimizer suite/coverage gates are not certified here.

Two new actual isolated-worker cases passed with production-size multi-frame preloads and nested expressions in interactive and bulk modes. Existing sandbox, checkpoint, preflight, capability and numeric regressions also ran. Bubblewrap/AppArmor protections were retained. Frontend XML records 152 successful Vitest cases; Node regressions, TypeScript/Vite production build, actual backend API checks and Python wheel/sdist builds passed. This is not browser visual/E2E or external TradingView execution evidence.

PineLib run 34000775224 and Ast2Python run 34000624343 independently passed both interpreter suites and published the exact library heads. The first PineLib delivery attempt dirtied tracked patch inputs before checkout; corrected preparation used a temporary directory without changing source or assertions. The compiler's first attempt lacked the not-yet-published pinned runtime; its unchanged candidate passed after dependency publication.

All joint verification jobs passed before release update. The first Actions publication push was rejected only for missing workflows permission on its token. The authorized connector rechecked the release base and exact tested commit/tree, then advanced RC6 with force=false. The publication retry succeeded, verified the existing head, archived the temporary ref and checked the final inventory. No repository permission settings were changed and no test was weakened.

## Artifact and branch receipts

Downloaded archives were SHA256-checked, JUnit parsed, source pins compared and the exact candidate Git bundles verified.

| Artifact | SHA256 |
| --- | --- |
| PineLib publication 9979418846 | `47429e0eea03e2d1cf11404b3553986ddc436e670d238ba65fcb1f3989bc00cf` |
| Ast2Python publication 9979442414 | `ed2ac64182e42cbf513f5865ce46ab317f9947e1044878483c611ca97805cb1c` |
| Joint Python 3.11 9979570234 | `a2934bf412ddd79f27fa6eac05299d31b86cc8d7a5607d33f145660b50995516` |
| Joint Python 3.13 9979585114 | `5364c15d604a1550281426a2ebf0992af05cd01bb3a18c2d768a5f42860ea435` |
| Frontend 9979593026 | `35debdf914addd11c47d5b3d0a6dee3132e097e381128a91ab82927e1fc5d670` |
| Successful publication retry 9979610762 | `e0cf330fafc3d6fa5408665bec5ee40fa7f130d193460f032ff8ae8309143dbf` |

OpenPine's receipt and fresh remote read show exactly main, release/v2.17, release/v4.0.2 and release/5.0.0rc6. Main and historical heads are unchanged. Same-name tags ops/rc6-merge-20260906 preserve maintenance heads: OpenPine `36bc63f327ec1523cb731e3516013e93523d6f69`, PineLib `755f5da40e74b6fd21843a11a717981f08a2140d`, Ast2Python `fa089ecd4b937c3ab0f9f09c56460ae9c16c0c0f`. Temporary publication scripts/patch submissions are not in the RC6 source tree. Other preexisting sibling branches were not deleted.

## Remaining acceptance

Update the exact source set together and recompile generated artifacts. The bootstrap change requires matching host/worker code. Historical documentation saying all nested requests are unavailable is superseded only for this tested subset. UDF/unsupported mutable or local capture, live/revised requests, FX and nonstandard contexts, automatic UI/CLI/gateway dataset loading, full isolated broker/IPC restart, complete strategy exits/trailing/risk/indexed trades and immutable production installation remain open. Static extraction/independent evaluation of nested syntax with dynamic_requests=false is not implemented: that form remains explicitly rejected.

The earlier standalone optimizer containment diagnostic and dependency-audit notices were not resolved by this merge; they remain documented in RC6_NUMERIC_REQUEST_VERIFICATION.md. Functional success is not full security, coverage, browser UX, external Pine v1-v6 oracle or 36-task acceptance. See RC6_NESTED_REQUEST_MERGE.md for the detailed implementation boundaries.
