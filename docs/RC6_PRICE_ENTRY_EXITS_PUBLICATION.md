# OP-07 / OP-20 — verified causal price-entry brackets

Date: 2026-09-06. This receipt supersedes the market-only limitation in the previous
exit-bracket report for the explicitly documented from_entry subset.

## Published and tested source

| Repository | Commit | Scope |
| --- | --- | --- |
| Backtest Engine | `891141faf482c76ed1f85a7f4b0076f26ed63336` | Forward-only price-event traversal and pending price-entry brackets |
| OpenPine | `f5710acc59ff650a816dbea8d17bd1004ad88df9` | Host surface, pinned broker and 37 compiled Pine/worker cases |
| OpenPine | `cc5f8d116660d40c6e42ffa1feef3cbcaada7d9e` | Retained permanent lint gate and implementation boundaries |

Engine base: `6b52242bd7887344386646ed9ab99227ca04d9df`.
Engine tested tree: `0660efd1474d264662333e2db54dda9f7fda38a2`.
OpenPine base: `f1cf2994a01b7003f976ba62844739dc104d659b`.
OpenPine tested tree: `e0a0434886938c39d8318700bf32ee51b6c95d10`.
This receipt, progress update and two ledger records are documentation only after
the tested source. They do not imply a separately observed full CI pass for that
later documentation commit.

Engine CI/publication: https://github.com/s7cret/backtest_engine/actions/runs/34029572996
Joint CI/publication: https://github.com/s7cret/openpine/actions/runs/34029925432

## What changed

An explicit exit submitted for an existing pending limit, stop or stop-limit entry
is now captured by that order instance and activates on its actual fill. Relative
TP/SL distances use the real fill and the already-admitted tick. Cancellation and
replacement preserve the previous no-resurrection, sizing and reservation rules.
Unqualified/all-entry persistence and repeated-entry relative allocation are not
silently introduced by broadening this supported entry-type subset.

The scanner follows the nearest eligible event along each monotone historical
price-path segment, then reevaluates the remaining orders after execution or
activation. A bracket created at an entry cannot see an earlier high/low. The
chart-close callback no longer replays consumed price segments, even when on-close
processing or fill recalculation is enabled. A dormant stop-limit's earlier limit
touch does not count as a later fill. A newly marketable stop-limit or absolute
bracket uses the current activation price under the configured gap policy.

For example, a modeled path 100 -> 98 -> 106 -> 102 with a long stop entry at 103
and protection at 99 must not close against the earlier low of 98. The position
stays open in the new test, with and without fill recalculation. Another scenario
activates a stop-limit only after its limit was touched; execution waits for a
subsequent valid crossing instead of rewinding the path.

OCA tests create a farther stop before a nearer one and verify the nearer reached
order wins. Limit-verification penetration changes the trigger threshold, not the
limit execution price. Gaps between chart bars or magnifier subbars remain discrete:
no synthetic prices are inserted through a gap. Close-only and observed-tick
execution do not acquire interpolated historical crossings.

Bar Magnifier no longer executes all subbar opens ahead of their high/low/close
segments. Only the initial chart opening is handled in the first open pass; later
subbar openings remain chronological. On-close execution uses the final chart close,
not an earlier subbar close. The new magnifier gap regression opens at 103 in the
first subbar, then exits at the second subbar's gap opening of 110, not at a fictional
intermediate TP of 105. Long/short mirror cases are checked.

The change is in the existing broker, not duplicated host trading semantics.
Trailing evolution is still owned by its existing scanner and is not newly certified.

## Observed verification

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 384 | 384 |
| PineLib | 229 | 229 |
| Pine2AST | 338 | 338 |
| Ast2Python | 372 | 372 |
| Backtest Engine | 680 | 680 |
| Optimizer | 281 | 281 |
| MarketData Provider, deterministic non-network | 601 | 601 |
| OpenPine native/affected-path functional cases | 904 | 904 |
| Functional total | **3789** | **3789** |
| Review ledger accounting, not conformance | 37 | 37 |
| Overall total | **3826** | **3826** |

Every executed selection has zero failures, errors and skips. These are the same
cases on two interpreters, not 7,652 distinct tests. Compared with the preceding
block, 95 functional cases were added: 58 broker and 37 OpenPine. Three obsolete
unsupported-price-entry assertions were upgraded to causal execution checks, not
removed or skipped. Full broker verification also passed independently before its
release update. Repeated standalone/local cases must not be added to joint totals.

Six new protected worker tests cover limit, stop and stop-limit in both interactive
and bulk modes, with on-close and fill-recalculation enabled. The in-memory transport
matrix additionally covers both directions and all four calculation-flag pairs;
five tests cover historical named when. Real compiled Pine, the existing runtime
and the actual broker are used. Tests compare complete intention tapes and broker
results, exact entry/exit prices and bars, quantities and remaining positions.
Bubblewrap/AppArmor protections were retained.

Six library functional suites are complete. The provider explicitly excludes five
external live-network tests, while OpenPine remains native plus an explicit affected
selection, not the entire project. Frontend has 152 passing Vitest and 22 passing
Node cases, successful TypeScript/Vite production build and API tests using OpenAPI
exported from the same backend. Changed-file Ruff, compileall and clean OpenPine
and engine wheel/sdist builds passed. Coverage gates, browser pixels and independent
TradingView execution-oracle acceptance are not established by these counts.

Local Python 3.13.5 verification included 680 broker cases and 31 new nonprocess
host cases. An additional broad local diagnostic hit nine existing worker tests
without local Bubblewrap; those environment failures are not included as passes.
The actual protected joint CI ran these paths successfully without adding skips or
weakening sandbox admission.

## Publication and independently checked artifacts

Both publications completed on their first attempts after all required checks.
Release refs were advanced normally, without force. Exact engine patch, tree and
commit identities were restored and verified in CI; host verification checked out
the exact source candidate created above the latest remote base. Downloaded bundles
were verified and the host source independently cloned; the tested host files match
the locally reviewed bytes and the advertised source tree.

Downloaded archive SHA256 values and all JUnit XML were checked independently:

| Artifact | SHA256 |
| --- | --- |
| Engine publication, 9988153307 | `28cf778cc73b6a4301bb4ca48d64f5ba7af60aaab785c0539b4065d204de4129` |
| Joint Python 3.13, 9988353271 | `1199579881f2d7552378eff82b3d4e59103cf6d91e043977da03a6516344781f` |
| Joint Python 3.11, 9988351472 | `9284e5759f016cd38641c359efbddf41e1f725d5e8337682c205c33df50fb4e2` |
| Frontend, 9988360852 | `cb3955532ea0a1604d0b403905a17e34cc3a9d45045c03d82521bd8628cb1be1` |
| Joint publication, 9988365571 | `94081c5344660f3095cf06f0ee6b5dd412d1eb69f43669e22bd92f4fdcc6a06e` |

Before/after receipts and a fresh GitHub read confirm exactly four OpenPine branches:
main, release/v2.17, release/v4.0.2 and release/5.0.0rc6. Main and historical heads
are unchanged. Same-name archive tag ops/rc6-price-exits-20260906 preserves maintenance
commit `6cce939edb0ab71498664a4160019e86c39b518f` in OpenPine and
`4872cdbf54b0a6411ec689dde8905db8d34198e8` in Backtest Engine. Temporary publication
code stays outside the RC6 source tree. No unrelated engine branches were removed.

Eight existing OpenPine runtime files match the baseline Git blobs exactly:
generated checkpoint, request data/requirements/transport, worker capabilities,
canonical bar decoding, isolated optimizer runner and admitted configuration.
The original 36 IDs, titles, acceptance states and specification checksum are kept;
only OP-07/OP-20 scope and remaining work are updated. Whole-task status remains
partial where larger requirements still exist, not a percentage of compatibility.

## Remaining limits and upgrade

Explicit from_entry remains required. Unqualified/all-entry exits, trailing,
repeated-entry relative levels, leg-specific metadata, mixed v6 absolute/relative
levels, risk commands and indexed trade methods remain open. Complete commission,
margin/liquidation, realtime/fill-phase and independent event-oracle acceptance is
separate. Full job recovery, automatic requested-series preparation and immutable
production installation are unchanged open work.

More crossed orders can mean more scanning work. No throughput, memory or full-run
speedup is claimed; this block prioritizes causal correctness. All examples are
synthetic specification regressions, not recorded TradingView output.

Update host, worker and the pinned engine together using RC6_LIFECYCLE_SOURCES.json,
and recompile artifacts for the changed host surface. Matching version strings are
not source identities. The evidence delivery is not a standalone production installer.
