# OP-26 / OP-27 / OP-12 — optimizer integration publication receipt

Date: 2026-09-06.

## Published source and verification

OpenPine tested source: `9328e45dc8d0aff8de1e4e7a0a9837afd785e2b8`.
Tree: `b7ea956e1ec220c7a6d9c8b9f25acbcf828f0af8`.
Base: `8d5c7bf26e293bd3007c98134cadd445198d23ee`.
Optimizer tested source: `95570459e50492dea8872b0a25f094e29d3e821f`.

Joint verification/publication: https://github.com/s7cret/openpine/actions/runs/34005041354
Optimizer verification/publication: https://github.com/s7cret/optimizer/actions/runs/34003198123

The seven source commits are preserved individually:

| Repository | Commit | Scope |
| --- | --- | --- |
| optimizer | `d2816781a7a020a7d32d9d61253ab27dca8a62b5` | OP-27 result and ranking integrity |
| optimizer | `facb6a05687a75eca2df7483558457f10033f840` | OP-26 warmup, input isolation and explicit identities |
| optimizer | `95570459e50492dea8872b0a25f094e29d3e821f` | Mapping fallback hash and strict-contract regression alignment |
| openpine | `eaa3727e68b42d452fffdecb2ddc665055b2e183` | OP-26 per-trial request data identity |
| openpine | `7b8b377cc7ca4abd07e1bf373fd7796f8a453886` | OP-12 permanent full optimizer suite and source pins |
| openpine | `01927b48e325405f47abe2f829f20789dae8aa07` | All-36 review ledger and accounting checks |
| openpine | `9328e45dc8d0aff8de1e4e7a0a9837afd785e2b8` | OP-26 broker metric computation binding |

## Actual test results

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 384 | 384 |
| PineLib | 229 | 229 |
| Pine2AST | 338 | 338 |
| Ast2Python | 372 | 372 |
| Backtest Engine | 586 | 586 |
| Optimizer | 281 | 281 |
| MarketData Provider, deterministic non-network selection | 601 | 601 |
| OpenPine native and affected-path functional cases | 822 | 822 |
| Review ledger consistency, not runtime conformance | 37 | 37 |
| Total | **3650** | **3650** |

Each interpreter ran 3,613 functional cases and 37 accounting checks, with zero
failures, errors or skipped cases. The same cases are repeated on two interpreters;
earlier standalone or local runs do not increase the unique-case total. Six library
functional suites are complete; the provider excludes five live-network cases, and
OpenPine is native plus selected regressions, not the complete project inventory.

Both real optimizer worker variants passed, including repeated/reordered and
concurrently launched trials in interactive and bulk modes. Protection was not
disabled. Frontend XML has 152 passing Vitest cases; Node regressions, TypeScript/Vite
build, actual backend OpenAPI checks, changed-source lint and Python wheel/sdist
builds also passed. These are not browser visual tests or external TradingView
execution-oracle comparisons. Coverage and immutable installation gates remain open.

Downloaded archives were verified against GitHub SHA256 digests, JUnit reports
parsed, and the publication Git bundle cloned independently. Its head and tree
match the exact tested source; its source pins match the release file.

| Artifact | SHA256 |
| --- | --- |
| Joint Python 3.13, 9980729152 | `5367095d90b73f92693587c1de2751bf80ee70cee7e72f8ccabace956361ea3d` |
| Joint Python 3.11, 9980730625 | `b9d05d2c6ac25668462f3804e0c964e6659e420a50b4a15f98d03f649adeb569` |
| Frontend, 9980736950 | `0c19ff56a570d5afc50d65c8356463aa68f6d64779f9ef6ab6738b04249ad8fd` |
| Successful publication retry, 9980748950 | `79eace2cad3c145ff74b67cd840353f86936f1ed3502f665af80ee12c59ee26c` |
| Optimizer publication, 9980117821 | `2197faa3a9e8168f658d085b47817baa32c9c4f3332bbc3e018dcbe98d24edf5` |

## Publication and failed-attempt accounting

All verification jobs passed before release publication. The first Actions push
could not update workflow files with its token. The authorized connected API then
advanced only RC6 with force=false, after checking the expected base and exact
tested commit. The publication retry verified that head and archived/deleted only
its temporary branch. No permission settings or protected historical heads changed.

Fresh reads and the final receipt show exactly four OpenPine branches. The tag
`ops/rc6-optimizer-20260906` preserves maintenance SHA
`68608afd64353612adf5c28572d0c3558bf2ff85`; the optimizer's same-name tag preserves
`8cc0ae5a4554daa033eb20d9fe03fbbbbbe6e4af`. One-shot patch submissions and publication
helpers remain in archived maintenance history, not the release source tree.

The preceding failed joint run was not silently relabeled: SHA-verified JUnit
reported one incorrect new test Bar type and two genuine optimizer failures due to
unsupported broker metric switches. The fixture now uses the public Bar converter,
and the host fixes the reproduced metric-binding defect. Existing route tests and
error checks were not weakened. An initial log-based ExecutionEvent hypothesis was
not confirmed by the downloaded reports and is not presented as an implemented fix.

This receipt and accompanying progress update are documentation only after the
tested code head. A subsequent documentation commit is not claimed to have a new
CI result unless separately observed.

## Implemented scope

OP-26: trial parameters include the effective warmup, including zero. Unsupported
warmup, range, seed, early-stop or output controls are rejected instead of silently
being dropped. The broker runner takes independent copies of nested parameters,
bars and supplied strategy instances. Engine factories still must create independent
engines; this does not prove arbitrary external/global state safe. Explicit production
identities use the strict SHA256 mode; development inference no longer reduces an
opaque object to its class alone or uses memory-address-bearing code repr strings.

OP-27: a failed, cancelled, incomplete or error-bearing result cannot be selected as
a completed profitable trial. Both recognized response contracts obey output/hash
validation. A nonfinite primary objective is not rankable. Optional undefined ratios
are omitted by normal extraction, while explicit required metrics still need usable
values. Real zeros in profit, profit factor and drawdown are not replaced by defaults.
Previously stored ranks are cleared before recomputation.

The host now rebinds verified request preloads to each new trial execution identity.
Only run/session IDs change; source envelopes, metadata and semantics remain unchanged.
The original manifest is validated before resealing, preventing corrupt/foreign data
from being made acceptable merely by assigning a new hash. Applied configuration and
persisted run identity use the rebound manifest. Both the source and trial hashes are
included in the response. The public host converter preserves a detached manifest.

The joint run exposed an additional integration error: result-field names such as
net_profit were passed to the broker's special-computation switch list. The broker
recorded REQUIRED_METRIC_UNSUPPORTED, which older response processing had ignored.
The host now validates numeric result fields against the broker result schema and
translates only the actual Sharpe/Sortino computation switches. It still rejects
unknown fields and missing/nonfinite required results. No error is suppressed to
make existing optimizer route tests pass.

Tests compare repeated/reordered sensitive-input trials with an ordinary backtest,
including exact intent tapes, trades, final equity and score-ledger hashes under the
same admitted context. Two protected-process cases launch serial and concurrent
trials through both interactive and bulk transports. Distinct run identities are
intentional; semantic fills and metrics are compared across those identities.

## Review accounting for all 36 tasks

RC6_REVIEW_36.json and RC6_REVIEW_36.md contain every original OP-01 through OP-35
heading, the original specification checksum, and the separately authorized OP-36
branch-consolidation task. Each row separates implemented work from remaining
whole-task acceptance and points to code, tests or a publication receipt.

The snapshot records 29 partial tasks, six requiring full verification, and one
accepted task: branch consolidation within s7cret/openpine only. These are whole-task
acceptance states, not percentages of implementation or Pine compatibility. Merely
rejecting an unsupported capability does not implement it. The 37 ledger consistency
cases validate IDs, paths and accounting rules, not TradingView semantics.

## Preservation and limitations

Existing nested-request execution, chunked transport, generated checkpoint v2,
canonical bar validation, numeric underflow rejection and negotiated worker
capabilities are retained. Six relevant runtime files match the preceding published
base byte for byte; the evidence includes their Git blob identities. Optimizer's
process_containment implementation and its dedicated tests are also unchanged.
Sandbox restrictions, process cleanup and timeout protection were not disabled.

This is not full acceptance of OP-26/OP-27: user-facing winning-trial replay,
locked training/validation/warmup, holdout verification, arbitrary seeded external
runner determinism and every runner type remain open. Production wheel identity,
all coverage gates, complete strategy exits/trailing/risk/indexed trade access,
request discovery/UDF/live contexts, complete job restart, browser UX and external
TradingView conformance are still separate acceptance work. No timing, memory or
whole-backtest speedup was measured in this block. Private copies improve isolation,
not guaranteed throughput.

The source pins in RC6_LIFECYCLE_SOURCES.json must be updated together; matching
package version strings alone do not establish identical code. This evidence archive
is not a self-contained production installer.
