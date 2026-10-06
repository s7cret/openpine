# INT05 negative reader and producer routing: review checkpoint

This bounded delta is based on the exact current integration candidate
`0bbcfc08206efa8325a6148390fe228785247e5d`, tree
`8dcc1d27875cc0905579d6505e8e8f2ac1c81de8`. It includes the previously reviewed
INT05 planner/collection changes and the central PineLib consumer policy edge.
The original seven library pins remain the control. The saved snapshot and its
environment/publication configuration have not been changed.

This document supersedes the execution forecasts and disk budget in
`INT05_QUALIFICATION.md`. That document retains historical base evidence. The
current collection is 26,190 obligations: OpenPine 11,998; Contracts 557;
Pine2AST 3,752; AST2Python 2,089; PineLib 5,370; Backtest Engine 1,241; provider
897; Optimizer 286. The five provider live cases are still mandatory separately.
The separate integrated INT05 owner inventory remains 76 nodes.

## Read-only negative sensitivity admission

`int05_tests/negative_sensitivity.py` compares three independently anchored
actual campaign executions: complete green control-full B, mutant full F(M),
and mutant affected A(M). It delegates green raw admission to the existing
aggregator. A bounded extension of `execution_campaign.py` exposes
`admit_failed_call_attempt`, using the same existing primary-artifact validator
with an explicit failed-CALL option. The normal green/default admission,
aggregator PASS decisions, planner APIs, CLI, policy and schemas are unchanged.
This owner API extension implements the parent-requested red-primary reader;
it is the only additional production-file delta from the integration candidate.

A red attempt must be the actual `a001` execution, return code 1, with no
infrastructure diagnostic or retry. Source, observed interpreter, task, shard,
run, plan and binding must match. Every assigned node must have exactly setup,
CALL and teardown, with setup/teardown passed, no skip or xfail, and valid
durations. Its phase errors must be exactly the stock semantic failure errors.
JUnit must contain exactly one failure for each admitted failed CALL and no
other failure/error/skip. Checksums, exact selectors, coverage data where
required, markers and instrumentation remain required.

The reader admits every complete shard and node, and permits only the standard
aggregate errors explained by those genuine red attempts. Missing, duplicated,
fragmented or aliased campaigns, collection errors, crash, invalid raw receipts,
source/producer/environment drift and unexpected A-only failures yield
`INCONCLUSIVE`. A full failure absent from A yields `FALSE_NEGATIVE`, even if
both campaigns are red. Confirmation requires the independently named existing
oracle to fail in F and every full failure to appear in A. The complete green
control inventory/instrumentation and all full owner gates must be retained.
Mutant bytes cannot carry the baseline producer SHA.

The final bounded regression passed **183 tests in 22.896 seconds**, including
23 negative-reader contracts and the existing execution-owner/planner/comparator
regressions; one ten-campaign performance self-test was explicitly deselected.
Ruff and diff validation passed. The 23 reader tests are outside the frozen
76-node integration owner inventory and were executed separately for this review.

`SENSITIVITY_CONFIRMED` is a negative audit result. Both mutant pytest campaigns
remain failed; it never grants stage, release or pytest acceptance. The existing
green affected/full comparator remains separate. The small actual campaigns in
`int05_reader_tests` are reader contracts, not product qualification.

## Actual producer-routing proof

The independently named real consumer oracle is
`tests/unit/test_broker_guard_edges.py::test_projection_uses_total_trade_counts_and_signed_position[FLAT-0.0]`.
The actual PineLib mutation changes `return value is na` to `return False` in
`pinelib/core/values.py`. Its committed, remotely advertised producer is
`d92b264d0a92c3c460a9068d0de1f4ed8c5320c5`, on
[the isolated proof branch](https://github.com/s7cret/pinelib/tree/implementation/int05-negative-proof-na-20261006).
No PR or hosted run was requested for that intentionally broken proof branch.

Two routes were collected and probed against actual detached Git worktrees:

| Route | Actual OpenPine producer | Final tracked selectors | A obligations |
| --- | --- | --- | ---: |
| Tracked pin route | `7c126d2f0f68d733488591497624f9e88df42fea` | PineLib code, OpenPine lifecycle pins and stack lock | 26,190 / all eight owners |
| External source-mode route | `0bbcfc08206efa8325a6148390fe228785247e5d` | PineLib code | 20,984 / PineLib, AST2Python, Backtest Engine, Optimizer, OpenPine |

[The tracked host proof branch](https://github.com/s7cret/openpine/tree/implementation/int05-negative-routing-proof-20261006)
updates both the actual PineLib revision and its package-tree identity. Existing
local committed-ref and structural stack-lock validators pass for that route.
Its complete final diff includes `openpine/stack-lock.json`; the planner correctly
expands A to full. The original one-file scope must not be substituted for this
actual routed scope.

The external source-mode route leaves tracked release metadata at B. It uses
actual clean Git HEADs, the stock source archive/provenance owners, actual stock
collection, `attest_ci_source_commits` for the checked eight-producer map, and
the existing `make_ci_plan`/runner. Runtime locators only select those actual
source roots and interpreter; they do not rewrite source, lock, node IDs or
raw evidence. The runner's existing `OPENPINE_SOURCE_ROOTS`/`PYTHONPATH` contract
loads those roots, and the existing compiler producer environment carries the
actual mutant revision. All eight imported namespaces and observed CPython
3.13.5 GIL identity were checked inside a real subprocess. The actual host build
identity came from its Git HEAD.

This source-mode proof is not `test-ci prepare`, a wheelhouse preparation, or a
coherent release-lock acceptance claim: its declared release PineLib SHA/tree
are still B and differ from the actual imported mutant. Any preparation/product
failure must remain visible and block the applicable acceptance. The parent
must review whether this source-mode negative experiment is an admissible
functional qualification route. Until then it cannot justify a narrowed real
candidate campaign or measured speed claim.

For both routes, a stock one-node smoke probe produced setup PASS, CALL FAIL,
teardown PASS. The exact B control produced three PASS phases. The new raw owner
admitted each actual red primary. These are targeted routing probes with an
explicit one-node probe policy, not complete A/F executions. Collection retained
all 26,190 normative nodes and unchanged per-owner hashes. Source archive,
eight Git bundles, source/environment/plan hashes, raw phases, JUnit and logs
are retained; the compact readback is in `INT05_ROUTING_REVIEW.json`.

## Execution review and resource budget

The required negative design is one actual complete B full campaign followed by
ten independently committed M candidates, each with literal actual F(M) and
A(M) receipts: **21 complete campaigns**. Equal task dictionaries prove scope
equivalence only; they cannot replace an actual A receipt. No optional three
extra green A campaigns are included. Every M must be recollected, attested and
externally anchored after its complete routed diff is known. Scope and workload
must be recomputed from that final diff, including pin/lock/metadata updates.

If source-mode routing is accepted and all ten final diffs retain the proven
one-file scopes, the planning workload would be 517,583 obligations. If all ten
tracked routes expand to full, it would be 549,990. Only the single real PineLib
routing case above is frozen now; these conditional totals are not executed
qualification or a final ten-candidate workload. The measured first B full run
must replace time/storage forecasts before scheduling the remaining twenty.

All future execution plans and the actual bounded probes have a stronger frozen
`disk_free_guard.minimum_free_bytes = 21474836480` (20 GiB). Native owner plans
with the historical 2 GiB default remain retained as planning inputs and are
not eligible for execution. Policy is unchanged; the supported existing plan
guard is resealed with the stricter explicit floor and a new external anchor.
Run a single heavy pool on the available 4 CPU / 16 GiB host, with the existing
runner resource controls. Current free disk is about 27 GiB: the usable growth
budget is about 7 GiB, not the earlier proposed 10 GiB. Keep all inputs, failed
logs, primaries and checkpoints. Delete only the attempt's reproducible private
temporary files after the existing primary validator has admitted success.

After the first actual complete calibration, record elapsed/CPU/RSS, disk
observations, retained bytes and the budget for all remaining raw evidence.
Stop for budget review before the remaining twenty if that budget cannot keep
20 GiB free. No automatic retry is permitted; checkpoint every attempt.

The heavy slot is released to INT05 after the parent's INT08 run `37529938988`
(6,318 PASS at `f02dfb6`). Reader/routing/design review is still required before
heavy campaigns or full builds, as explicitly requested by the parent. No heavy
campaign, full build, hosted launch, merge or release has occurred here.
Full owner acceptance, provider live 5, and the remaining 68-requirement spec
are open. Functional sensitivity cannot waive the existing full owner gates.
The new reader commit is a review delta, not a recollected full candidate. The
integrator must refresh its package identity and freeze the resulting exact
candidate before full acceptance; the frozen routing probes above remain bound
to their stated original Git producers.
