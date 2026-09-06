# OP-07 / OP-20 — verified causal trailing exits

Date: 2026-09-06.

## Published source

The four original source commits are published in their respective RC6 branches, without squash or changed identities. OpenPine base is `7d7470fac1a0f92b105e1d2d87c1e4bdbeb2e924`; tested head is `eb56964d75ff371a0755c34a3a92e146c1c78070`, tree `a90f8a0600583d72846b0ca6316acc41f7e6239d`. This receipt and its progress update change documentation only, not the tested runtime, UI or permanent workflow.

| Repository | Commit | Scope |
| --- | --- | --- |
| Contracts | `9aa381dc0c1570fda1ea90221d9148194e506279` | Explicit 2.5.0 trailing intent, generator and contract tests |
| Engine | `d69ddc32a30778c04e5065169477fe3302aea060` | Causal price events, per-fill trails, amendments and native resume |
| Engine | `38818c614a425902f881321faec311f03009c810` | Versioned bridge and complete-parameter validation |
| OpenPine | `eb56964d75ff371a0755c34a3a92e146c1c78070` | Pinned stack, host admission and mandatory process tests |

Contracts verification/publication: https://github.com/s7cret/openpine-contracts/actions/runs/34041406472
Engine verification/publication: https://github.com/s7cret/backtest_engine/actions/runs/34041912588
Joint verification/publication: https://github.com/s7cret/openpine/actions/runs/34041985679

## Implemented behavior

The existing price-event scanner now considers trailing activation and adverse stop crossings alongside fixed orders. It processes the nearest forward event rather than delaying a trailing stop until the next OHLC endpoint. A price already traversed before entry cannot be reused to activate or close that new entry. The reproduced persistent-exit defect is also fixed: an entry under an existing all-entry policy observes its actual fill price before the path continues, even without fill recalculation.

Each actual entry fill has independent activation, best observed price and reservation. Reissuing an active trail modifies it in place instead of adding a duplicate order and losing the best observed price. Explicit offset amendments use that saved best price; the historical path is not replayed. Gaps execute at observed opens, not interpolated stop levels. Zero offset remains meaningful; incomplete, negative or nonfinite offsets are not silently inferred or defaulted.

Compiled Pine v6 selects the first-triggering activation from trail_price/trail_points. The v1-v5 regression matrix retains absolute priority. Native callers select the policy explicitly, with absolute_first as the default. Relative levels use actual entry fills and admitted mintick. Pending market, limit, stop and stop-limit entries, named/all-entry scope and partial quantities are covered by actual compiled tests in both transports.

Synthetic example: entry 100, mintick 0.01, trail_price 110, trail_points 500 and trail_offset 200. V6 activates at 105 and exits at 106 after a high of 108 and a pullback. V5 waits for absolute activation and exits at 111 after a later high of 113. Another test uses repeated IDs with 2 units at 100 and 6 at 110: activation distance 15, offset 2 and 50% quantity produce exits of 1 at 118 and 3 at 128 after separate highs of 120 and 130, leaving 1 and 3. Long and short scenarios are tested. These are explicitly constructed regression expectations, not exported TradingView trades.

## Supported subset and compatibility

The compiled bridge supports trailing alone or trailing with a fixed take-profit. A fixed stop/loss plus trailing in the same call is still rejected: arbitration between those two stop mechanisms is not implemented. Per-leg metadata, full FIFO/ANY attribution, named absolute repeated-entry quantities, risk/indexed methods, complete historical signatures and realtime/margin interactions remain open. Active-trail amendments are tested native behavior, not independently verified TradingView behavior for every amendment combination.

ExitIntent 2.5.0 requires explicit price_pair_policy, nonnegative trail_offset, an activation parameter and exactly one named/all-entry scope. Old 2.2/2.3/2.4 validation is retained; the 2.4 policy still must be first_trigger. The schema generator matches the checked-in schema. Old readers cannot consume the new 2.5.0 form. Update host, worker, Engine and Contracts together using RC6_LIFECYCLE_SOURCES.json and recompile generated artifacts. Matching package version strings do not identify matching code.

Native broker snapshots retain activation and trail_best_price plus persistent all-entry policy. Resume tests compare fills, closed trades and equity with uninterrupted execution. This is not full isolated-job broker/IPC/worker restart recovery, and no complete OP-10 acceptance is claimed.

## Verification actually observed

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 437 | 437 |
| PineLib | 229 | 229 |
| Pine2AST | 338 | 338 |
| Ast2Python | 372 | 372 |
| Backtest Engine | 848 | 848 |
| Optimizer | 281 | 281 |
| MarketData Provider, deterministic non-network selection | 601 | 601 |
| OpenPine functional native / affected-path selection | 1009 | 1009 |
| Functional total | **4115** | **4115** |
| Review accounting, not semantic conformance | 37 | 37 |
| Total | **4152** | **4152** |

Both downloaded joint JUnit sets have zero failures, errors and skipped cases. New functional cases: 25 Contracts + 55 Engine + 28 host = 108. Two former unsupported-trailing expectations are replaced with incomplete/unsupported-combination checks without changing their count; no scenario was skipped to force a pass. Historical, standalone and repeated interpreter executions are not added as unique coverage.

Four new actual protected-worker cases cover interactive/bulk and process_orders_on_close off/on with calc_on_order_fills enabled. All four passed on both interpreters. Bubblewrap/AppArmor was not disabled. The six library functional suites are complete. The provider explicitly deselects five live-network cases; host coverage is native plus the explicit regression inventory, not every OpenPine test.

Frontend artifact XML contains 152 passing Vitest cases, and its Node log records 22 passing cases, zero failures. The actual TypeScript/Vite production build, same-backend OpenAPI checks, permanent changed-source Ruff gate, compileall and Python wheel/sdist builds passed. A later documentation SHA or automatic repeat is not claimed to have completed unless separately observed. Browser/canvas visual acceptance, coverage thresholds, immutable production installation and external TradingView execution remain separate gates.

## Independent evidence verification

Downloaded archive hashes match GitHub artifact digests. JUnit test cases were counted directly; the four new process variants were located explicitly. Contracts, Engine and host Git bundles were restored in separate local repositories. Each reviewed raw commit, source tree, patch checksum and source-pin file was compared with the exact local source series. The final publication bundle matches the tested host bundle.

| Artifact | SHA256 |
| --- | --- |
| Contracts publication, 9991781080 | `e2ff8675566170fbcb28d4ba55d50ab772341638515e6e464b1fbb68de5b28b6` |
| Engine publication, 9991936837 | `08ad410d34dc941c105927edd083a3da00cd7bd73de1647b3da1a8a57bce7cbf` |
| Joint Python 3.11, 9992081096 | `b940d5c778bb9ea6ee32f00b4ec3af0435b21eb632c9d33f3b89717224a263ba` |
| Joint Python 3.13, 9992070181 | `b16dff943d7622c43b8a552d2d9c0c557f5dbd14afccfc10c245901b36a9950f` |
| Frontend, 9992089924 | `6603aef745d133102ce0c1f9795a81f4864373f121381854ae7f3425ac634736` |
| Successful joint publication retry, 9992155024 | `db897ed8f9f6439d9beeba3c321249f7dbb046d41a17636cd95ed5bb006447e8` |

## Publication and preservation

The initial Engine verification stopped before installation because an uploaded bridge patch lacked its final context line. The missing transport line was restored; the original patch, tree and source commit checksums were not changed. No source/test assertion was weakened to resolve that delivery failure.

All joint Python and frontend verification jobs succeeded before the release update. Actions then rejected the release push because its token could not update rc6-native.yml. The connected GitHub API advanced the already verified commit with force=false after a fresh base and tree check. The publication retry verified the expected head and archived/deleted only its temporary branch. No account permissions were changed.

The final receipt contains exactly four OpenPine branches: main, release/v2.17, release/v4.0.2 and release/5.0.0rc6. Main and historical heads are unchanged. Tags ops/rc6-trailing-20260906 preserve the maintenance tips: Contracts `91f53004be56298f0a3ffcb02c2108672e047f9f`, Engine `6d78b281c24e868f1e715f5f68504396f9d648b7`, OpenPine `d7c8c43269249a712661fbf60e2b855cf0b6067b`. One-shot publication helpers are not in the RC6 runtime tree.

Nine important host files (request data/transport, generated checkpoints, worker runtime/capabilities, canonical marketdata, configuration, isolated worker and optimizer runner) and the UI tree match the prior baseline byte for byte. Their Git blob identities are included in delivery evidence. Previous work was not replaced with stale source.

## All-36 task accounting

This receipt supersedes the wholly-unimplemented trailing wording in the prior mixed-exit snapshot for OP-07/20 only. Implemented: the standalone/TP-plus-trailing subset, versioned activation, causal per-fill execution, repeated-order state and native resume. Remaining: fixed stop/trail competition, full amendment conformance, per-leg messages, FIFO/ANY, remaining quantity cases, risk/indexed methods and complete realtime/margin/version coverage. The complete all-36 delivery snapshot preserves the original task IDs/titles and those remaining criteria; whole-task states remain 29 partial, six unverified and one accepted (OpenPine branch consolidation only).

No full TradingView 1:1, complete 36-task closure, performance speedup or autonomous production installer is claimed. Additional activation boundaries can increase scanning work. Automatic requested-series loading, unsupported UDF/live requests, complete restart, immutable delivery, winner replay/holdout and browser UX remain separate tasks.

Official semantic references, not external execution evidence:
- https://www.tradingview.com/pine-script-docs/concepts/strategies/
- https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/
