# OP-07 / OP-09 / OP-20 — verified market brackets and exit sizing

Date: 2026-09-06. This receipt supersedes the local-only verification notes in
RC6_EXIT_BRACKETS_BLOCK.md, not its deliberate implementation limits.

## Published source

OpenPine tested head: `0ddd7b32e91feedf4be377d07cf257d788e5b52f`.
Tree: `e81706404d9eccc85d294186befd09cfb62249b6`.
Base: `ffee029e1fc44ab907ea61313f1bd37ef58d2989`.
Backtest Engine head: `6b52242bd7887344386646ed9ab99227ca04d9df`.
Engine tree: `25d3e6d296dec779c4da63fa1f760f6dd3d22709`.
Engine base: `70dd7ccfecaabafa3000d359770305acb32c8b9e`.

| Repository | Commit | Change |
| --- | --- | --- |
| backtest_engine | `f533c8555281422af2887f3d904032ced34ff4dc` | OP-20: capture exits on pending market entry instances |
| backtest_engine | `9492f3f411fbce1691fa3123620d62ede7bb1391` | OP-20: initial entry quantity, replacement and repeated-ID reservations |
| backtest_engine | `6ca6a0deccdcc1922594834c881e675c8edcdc1c` | OP-20: activate pre-submitted brackets at the entry fill point |
| backtest_engine | `6b52242bd7887344386646ed9ab99227ca04d9df` | OP-07: broker-owned binding instead of a premature host rejection |
| openpine | `fe300627d26ac0216342bd397db41b676c593b4d` | OP-09: resolve the admitted tick in both broker transports |
| openpine | `df50d40b6cdf52f057c205463460bad10bb72858` | OP-07/20: host surface, pinned engine and compiled Pine regressions |
| openpine | `0ddd7b32e91feedf4be377d07cf257d788e5b52f` | Permanent checks and original 36-task ledger update |

These exact seven commits are published, not merely held in a local archive.
Subsequent receipt/progress changes are documentation only. They do not imply a
separate observed CI pass for the later documentation commit.

## What is now covered

An explicit exit submitted after a pending market entry in the same callback no
longer disappears or fails just because the pre-callback position is flat. It is
attached to that order instance and activates on the actual opening fill. Cancelling
an exit/entry or replacing its binding does not resurrect the instruction on a
later unrelated entry reusing its ID. Relative levels use the actual entry price.
Gaps beyond already submitted absolute protection are handled at the same opening
price point, even when the script does not recalculate after fills.

Example verified with admitted mintick 0.01: profit=500 means a five-unit distance.
An entry submitted at close 100 but filled at open 110 has a target of 115, not
105 or 610. The earlier host could let the broker infer mintick=1 from integer
OHLC while Pine saw 0.01. Both transports now use the admitted instrument tick;
conflicting explicit ticks and invalid numeric values fail before worker staging.
Resolved defaults enter the effective configuration hash without changing the
submitted payload or bypassing its original checksum.

The reservation regression starts with ten units and an exit reserving eight.
Reducing that exit to three frees seven for a second exit; actual fills are seven
and three. Named-entry percentages no longer size from unrelated positions, and
initial entry quantity survives partial closures. Two pyramid entries of size two
and three sharing one ID reserve a total of five, not only the last trade's three.

All trade semantics remain owned by the existing broker. The original full broker
suite passes, including earlier partial-exit and diagnostic behavior. The changed
host test helper resolves the broker config as production does; it does not replace
the actual Pine compiler/runtime/broker or weaken the old assertions.

## Observed verification

Engine CI: https://github.com/s7cret/backtest_engine/actions/runs/34026186046
Joint CI/publication: https://github.com/s7cret/openpine/actions/runs/34026971028

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 384 | 384 |
| PineLib | 229 | 229 |
| Pine2AST | 338 | 338 |
| Ast2Python | 372 | 372 |
| Backtest Engine | 622 | 622 |
| Optimizer | 281 | 281 |
| MarketData Provider, deterministic non-network | 601 | 601 |
| OpenPine native/affected-path functional cases | 867 | 867 |
| Functional total | **3694** | **3694** |
| Review-ledger consistency (not Pine conformance) | 37 | 37 |
| Overall total | **3731** | **3731** |

The XML reports have zero failures, errors and skips in the executed selections.
The 81 added functional cases comprise 36 broker and 45 host cases; four host cases
use actual protected processes with fill recalculation, interactive/bulk and
on-close enabled/disabled. They all passed on both interpreters. Local repetitions
and standalone engine CI overlap the joint totals and are not additional unique
tests. Bubblewrap/AppArmor were not disabled and no skips were added to make this pass.

Six library suites are complete functional runs. Five external provider network
cases are excluded explicitly; OpenPine is native plus selected regressions, not
its entire project inventory. Existing optimizer containment and serial/concurrent
worker cases also ran. Frontend evidence has 152 passing Vitest cases and 22 Node
cases; TypeScript, Vite production build and API checks against the same backend's
exported OpenAPI pass. Changed-source Ruff, compileall and clean wheel/sdist builds
also pass. Coverage thresholds, browser visuals and external TradingView execution
are not certified by these counts. No performance improvement was benchmarked.

## Publication and independent artifact checks

All verification jobs passed on their first attempt. The subsequent Actions release
push was denied only because its token lacked permission to update rc6-native.yml.
After checking the expected remote base and exact tested commit, the authorized
GitHub connector advanced RC6 with force=false. Retrying publication verified that
same head, archived the maintenance tip and removed its branch. No repository
permissions, branch protections or test assertions were changed.

The downloaded archives were SHA256 checked, JUnit reports parsed, and Git bundles
verified against the original local commit/tree identities. Before/after receipts
confirm preservation of main and historical heads. OpenPine has exactly main,
release/v2.17, release/v4.0.2 and release/5.0.0rc6. The same-name archive tag
ops/rc6-exits-20260906 retains `afc6d2b885fcee5bbde504f127480dee4bf699d4`
in OpenPine and `36625c6c230dbe7b30724863b22188db6a48559f` in the engine.
No unrelated engine branches were deleted. One-shot publisher code is confined to
archived maintenance history, not the release source tree.

| Downloaded artifact | SHA256 |
| --- | --- |
| Engine publication/3.13, 9987129116 | `f60bec116e8be4212f3ed8676e16fb41f3c3517d93d68a224054b8806f36dc21` |
| Engine 3.11, 9987125436 | `9430cc81c023c748342b32452509a0da23a79e422f503b1164c7d48e43fb2140` |
| Joint 3.13, 9987449896 | `11661b23edbe82408f2fdad09153f98def6352619c34e70ea6cf56e411db8f94` |
| Joint 3.11, 9987455075 | `54ee9f681ada4d049f50441f87f66146e343b512f6a990d7e355587b557a0cd0` |
| Frontend, 9987463305 | `2edf1cd7b97b4cc8b9e8cd681f93eab30a39256bfccf3595093f9789c01edee2` |
| Successful publication, 9987487047 | `3c4afbf24b601c6f7f3d619fdb12e500f5cac1e51fc797733ca9c1900e3c9185` |

Seven existing runtime files (generated checkpoint, request data/requirements/
transport, worker capabilities, canonical bar decoding, isolated optimizer runner)
match their preceding published Git blobs exactly. Original task IDs, titles,
statuses and the source-spec checksum are preserved. The latest implemented scope
is recorded in RC6_REVIEW_36.json and RC6_REVIEW_36.md; whole-task acceptance remains
29 partial, six requiring verification, one accepted (OpenPine branch consolidation).

## Remaining limits

This is a verified market-entry bracket subset, not complete strategy.exit parity.
Pending limit/stop/stop-limit entries are explicitly rejected until continuous
price-segment activation is implemented. Deferred relative exits across repeated
entry IDs require per-trade levels and are also rejected. All-entry/unqualified
persistence, trailing, per-leg metadata, v6 mixed relative/absolute levels, risk
commands and indexed trade methods remain open. Omitted-leg replacement semantics
were not changed. Full commission/margin/fill-phase and external event-oracle
acceptance is not implied by the tested examples.

Old broker resume snapshots do not establish the new initial-entry-size provenance;
full isolated-job recovery is still unsupported and not advertised. Automatic
requested-series loading, UDF/live requests, immutable installation, browser UX
and the full Pine v1-v6 conformance corpus remain separate work. The original broad
36-task acceptance is not closed by these fixes.

Update the host, worker and engine together using RC6_LIFECYCLE_SOURCES.json and
recompile after the host capability surface changes. Same package version strings
are not source identities. The evidence archive contains patches and source history,
not a self-contained production installer.
