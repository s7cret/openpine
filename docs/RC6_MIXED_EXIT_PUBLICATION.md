# OP-07 / OP-20 — mixed fixed exit prices: verified publication

Date: 2026-09-06.

## Published source and repeat verification

OpenPine tested source: `4ec4d177bf0d0dbc0ffc2891f79332ac2bb6785c`.
Tree: `75e1f6ba2f61b5806f7bd93166e2f5ae013e36e6`.
Base: `7cc85acc61f5db8eb329839af90cc281a39e8f73`.

| Repository | Source commit | Scope |
| --- | --- | --- |
| openpine-contracts | `db372bc7b135a084667d3c726eca0465d21f008b` | Versioned 2.4.0 price-pair policy, generator and contract regressions |
| backtest_engine | `3e5271671afdd41d9cba7400ac70fc569edeb6ad` | Per-fill fixed TP/SL arbitration, intent replay and native policy persistence |
| openpine | `fcb802d3c9536d31245c9348053f7c4dc614509f` | Published dependency pins, host admission and compiled/process version matrix |
| openpine | `4ec4d177bf0d0dbc0ffc2891f79332ac2bb6785c` | Permanent mixed-price checks and precise compatibility notes |

Joint publication: https://github.com/s7cret/openpine/actions/runs/34037631453
Permanent repeat on the same source: https://github.com/s7cret/openpine/actions/runs/34038152756
Engine verification: https://github.com/s7cret/backtest_engine/actions/runs/34037330744
Contracts verification: https://github.com/s7cret/openpine-contracts/actions/runs/34036960698

Both joint verification and the permanent repeat succeeded on Python 3.11/3.13 and frontend. Original commits, trees, patches, source pins and publication receipts were independently checked against downloaded artifacts. This receipt and the accompanying all-36 status corrections are documentation only; they do not modify the tested runtime, UI or CI. A later documentation SHA does not imply a separately observed full rerun.

## Implemented behavior

A Pine v6 exit with active profit+limit or loss+stop parameters now emits an explicit `first_trigger` policy. The broker evaluates candidate levels at each actual entry fill and materializes one TP and one SL, not duplicate competing legs. Relative distances use that fill's price and admitted mintick. Long TP selects the lower candidate and long SL the higher; short positions mirror these choices. Gap execution still belongs to the existing forward-only price scanner.

V1-v5 compiled regression cases and old replay records keep absolute precedence. Native `StrategyContext.exit` defaults to `absolute_first`; native callers explicitly select `first_trigger`, because that API has no implicit Pine-version context. Zero is a real tick distance. A missing/NA member is omitted, not converted to zero; the special 2.4.0 policy is emitted only when an active pair remains.

The host no longer rejects the supported fixed mixed-price forms at admission. Previously unqualified exits and pending market/limit/stop/stop-limit entries remain supported. Per-fill targets, partial quantities, OCA reservations, cancellation/replacement and all-entry position lifetime use the existing broker paths. No parallel broker or alternative configuration converter was introduced.

Example from actual compiled-Pine and native regressions: long entries of 2 at 100 and 6 at 110, mintick=1, profit=20, limit=125 and qty_percent=50 produce exits of 1 at 120 and 3 at 125 under the v6 policy, leaving 1 and 3. The first lot selects its relative target; the second selects the absolute target. Native resume tests preserve this policy for subsequent entries and compare fills, closed trades and equity with uninterrupted execution. These are explicit synthetic regression expectations, not exported TradingView execution results.

## Wire compatibility

ExitIntent 2.4.0 requires `price_pair_policy=first_trigger`, at least one active fixed pair and exactly one named or all-entry scope. Ambiguous scope, wrong policy, inactive pairs and attempts to use the version for other intent kinds are rejected. Existing 2.2.0 named and 2.3.0 all-entry formats remain valid and cannot silently acquire the new interpretation. The schema generator and checked-in schema agree.

Update host, worker, Contracts and Engine together using `RC6_LIFECYCLE_SOURCES.json`, and recompile modules against the updated host surface. Old readers cannot consume 2.4.0 mixed exits. Identical package version strings do not establish identical source code. The delivered evidence is not an autonomous installer or immutable production release.

## Tests actually observed

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 412 | 412 |
| PineLib | 229 | 229 |
| Pine2AST | 338 | 338 |
| Ast2Python | 372 | 372 |
| Backtest Engine | 793 | 793 |
| Optimizer | 281 | 281 |
| MarketData Provider, deterministic non-network selection | 601 | 601 |
| OpenPine functional native / selected regressions | 981 | 981 |
| Functional total | **4007** | **4007** |
| Review accounting checks, not semantic conformance | 37 | 37 |
| Total | **4044** | **4044** |

Downloaded JUnit XML from both joint jobs and both permanent repeat jobs reports zero failures, errors or skips. The same cases repeat; totals must not be added as unique coverage. Compared with the previous accepted selection, the net increase is 125 functional cases: +17 Contracts, +69 Engine and +39 host. New files contain 127 cases (17, 70, 40); two old unsupported-mixed-price expectations were replaced by working behavior, not skipped.

Four new mandatory process tests exercise both interactive/bulk and on-close off/on with fill recalculation, two entries, both fixed price pairs and real resulting exits. Existing protected-process and optimizer tests also ran. Bubblewrap/AppArmor protections were retained. Frontend publication XML has 152 passing Vitest cases; the downloaded Node log records 22 passing cases. Actual TypeScript/Vite build and current backend API checks passed, as did changed-source lint and Python wheel/sdist builds. The permanent repeat's job steps independently confirm frontend success.

Six library functional suites are complete. Five provider live-network cases are explicitly excluded, and OpenPine uses native plus its explicit affected-path inventory, not every project test. Browser/canvas visual testing, complete coverage thresholds and an independent TradingView oracle remain separate acceptance. No timing or whole-backtest memory/speed claim is made.

## Integrity and branch preservation

The three downloaded Git bundles were restored into separate repositories. Every source patch SHA256, raw commit and tree SHA matches the reviewed plan; bundle verification and published-head receipts agree. Source pins in both permanent CI archives match the publication and identify the same tested OpenPine head.

The final publication receipt confirms four OpenPine branches, with main and historical releases unchanged. Temporary `ops/rc6-mixed-exits-20260906` refs were archived as same-name tags and removed. Preserved maintenance tips: OpenPine `35c22662aa6edc10818b83d5564b99799f875e44`, Engine `806167bf26a3341505d57b5785642b4541c3742a`, Contracts `3a9a56c5f1cd613a2bacd3f12f260447bdf1f6c5`. The successful publication is the observed second run attempt; it is not described as first-attempt success.

Nine important host files are byte-identical to the previous all-entry source: request_data, request_transport, generated_checkpoint, rc6_worker_runtime, worker_capabilities, rc6_marketdata, rc6_config, isolated_worker and optimizer/isolated_runner. The UI tree is unchanged. The evidence records exact Git blob identities. No earlier request/checkpoint, metadata or optimizer implementation was replaced by stale source.

| Downloaded artifact | SHA256 |
| --- | --- |
| Joint publication/3.13, 9990822792 | `a302123cbce5a788b4c8858b8c0bd3277b2645eabc92abc05383161b5848df1f` |
| Joint 3.11, 9990758021 | `8fba3c6a32d73a0b2d1c4bc100be8f0cdcca10cc239122496ee14e453abded16` |
| Engine publication, 9990587976 | `9aefefcfc1ea0278f19122274d034e0b87f52c2974fb204047196f8e9039a504` |
| Contracts publication, 9990471580 | `cdcb39554326227cf142ce2313107af4630eddeb141043501be432377c789b4f` |
| Joint frontend, 9990789419 | `21a69ddc92c2e4c075d426184461b3f70913c38ebd06e03e0067992cd362bb66` |
| Permanent 3.11, 9990945616 | `2c5780fcd908249511847facdf901e16ff572e799b414ada2f30d58ba46a96b7` |
| Permanent 3.13, 9990917355 | `e5f208fd71961735b718adb5e3d5729fb60ebd69f1ee2af8e8c04b16becd02e8` |

## Remaining acceptance

This closes the documented fixed profit/limit and loss/stop mixed-price gap, not all OP-07/20. Trailing and its activation pairs, per-leg comments/alerts, FIFO/ANY attribution, named absolute repeated-entry quantity semantics, risk/indexed methods, complete historical signatures and full margin/realtime interactions remain open. Native broker resume is not complete isolated-worker/IPC restart recovery.

The all-36 ledger updates only the implemented portions and remaining boundaries of OP-07/20. Whole-task statuses remain 29 partial, six unverified and one accepted (OpenPine branch consolidation). Full request discovery/UDF/live contexts, immutable delivery, winning-trial replay/holdout, browser UX and independent Pine v1-v6 conformance are not completed by this block.

Official semantic references used for comparison, not external execution evidence:
- https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/
- https://www.tradingview.com/pine-script-docs/concepts/strategies/
