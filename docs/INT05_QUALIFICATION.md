# INT05: qualified profile boundaries, parent review checkpoint

This bounded package starts at OpenPine commit
`3fbd28568948340bb75def1db2f882d3d65ce72c`, tree
`8a8f7086c435c85977fa817fbf542831741e4394`, and preserves all seven library
pins, including PineLib `b953e803d618a9784b117f8047ca709791aaca79`.
The remaining-spec source was verified against SHA256
`3f09ed3901f8ecf1262ffa983c6eaf52a51ceaf2087abe98db2093d45d9ca622`.
Execution uses ordinary CPython 3.13.5 with enabled GIL, a separate editable
environment, and verified imports from eight separate worktrees.

## Implementation and independent negative tests

The existing planner and collection joiner now reject ambiguous selector
containers and dependency declarations. Unknown or noncanonical changed paths
select the full inventory. An affected path absent from the frozen source
manifest, including a deleted path, also selects the full inventory.
Policies may broaden the shared-input boundary; fixture, schema, catalog, ABI,
state, generator and verification-selector changes cannot narrow it.

Non-smoke plans must retain their full collected node hash. Preparation owners
cannot simultaneously be recorded as test obligations. Collection joins reject
unsafe node IDs, incomplete marker evidence, unused interpreter slots and
missing policy identities. Scoped profiles require compatible declared
interpreters. Existing `make_plan` / `select_components` signatures and task-plan
schemas are unchanged. Smoke still matches exact collected node IDs, including
opaque parameter IDs, and a removed ID fails closed.

A real consumer probe found a missing policy edge: Backtest Engine imports
PineLib from `strategy_capabilities.py` and `delegated_strategy_intents.py`.
Changing PineLib's `is_na` broke the existing Backtest Engine projection test
while the old graph omitted Backtest Engine and Optimizer. An observed-boundary
dependency floor in the planner now includes both consumer suites and records
PineLib as preparation for a Backtest Engine component/smoke plan. The reserved
policy file remains untouched; its owner should reconcile this declaration.
The independently failing pre-fix scope test and real failing consumer oracle
are retained in the checkpoint evidence.

`int05_tests/test_profiles.py` records explicit expected boundaries separately
from the selection algorithm. On the unmodified base these tests produced
28 failures / 19 passes. Two later boundary contracts cover the observed runtime
edge and preparation split. Small synthetic sources
in these guard tests are verifier fixtures, not product qualification.

## Real product probes and retained collection scope

`int05_tests/patch_scenarios.json` specifies ten concrete mutations: local
timezone semantics, provider consumer contract, cross-owner runtime NA semantics, shared EMA checkpoint fixture,
execution-event schema, runtime catalog, ABI projection, checkpoint NA state,
artifact generator and transitive consumer selection. Each has an independently
named existing product oracle. `test_real_patch_oracles.py` copies only the
changed owner to a private root, proves the unmodified oracle passes, applies
the mutation, and proves the same oracle fails. All ten probes passed their
control/mutation checks. All eight existing policy smoke selections executed
and passed. These probes are targeted evidence; complete affected/full campaign
comparison is still required before INT05 can be marked qualified.

The final targeted regression passed 153 tests in 14.42 seconds; one existing
ten-campaign performance self-test was explicitly left out of this bounded
selection. Lint and diff validation passed. The final ten real patch probes and
eight real smoke checks are reported separately to avoid summing repeated runs.

The existing `test-collect` command verified the complete current policy scope:

| Owner | Collected obligations | Deselected |
| --- | ---: | ---: |
| OpenPine Contracts | 557 | 0 |
| Pine2AST | 3,752 | 0 |
| AST2Python | 2,089 | 0 |
| PineLib | 5,370 | 0 |
| Backtest Engine | 1,241 | 0 |
| Marketdata Provider | 897 | 5 |
| Optimizer | 286 | 0 |
| OpenPine | 11,855 | 0 |
| Total | 26,047 | 5 |

OpenPine's policy scope includes `rc6_tests` and its frozen selected regressions;
it is not every file under `tests`. The provider's five live-network tests remain
mandatory unresolved obligations for final acceptance. No marker policy,
inventory baseline, full acceptance gate or shared schema was changed here.
INT05 tests live outside `rc6_tests` so this package does not silently rebaseline
the normative inventory.

## Reproduce counts, hashes and scopes without execution

Collect with the existing command, freeze real owner locations with the existing
`freeze_owner_launch`, then run:

```bash
python int05_tests/reproduce_profiles.py \
  --policy verification/execution-policy.json \
  --collection /external/evidence/collection.json \
  --owner-launch /external/evidence/owner-launch.json \
  --output /external/evidence/profile-plans-attempt-001
```

This emits actual existing-schema plans for all 60 scenario/profile pairs,
deduplicated by content hash, plus `counts-hash-scope.json`. Each row names
component test obligations, their collected and selected hashes/counts,
preparation owners, required owner gates and planning time. Source, environment,
policy and independently reviewed collection identities are rechecked. This
command does not execute tests or acceptance gates and rejects stale source.

For example, the provider consumer-boundary affected scope contains provider,
optimizer and OpenPine: 13,038 obligations. The narrow OpenPine scope contains
11,855 obligations and seven preparation owners. Shared semantic changes and
integration/stage-full/release-full retain all 26,047 current policy obligations.
An owner smoke contains its exact policy node; component contains the owner's
complete collected suite. These counts do not assert a runtime speedup.

## Compare actual affected and full campaigns after parent review

Use the existing `test-collect`, `test-plan`, `test-run`, and aggregator on each
real patch candidate. Keep identical frozen source, observed interpreter,
policy, reviewed inventories and instrumentation for its affected/full pair.
Supply explicit independent expected owners from the scenario manifest. Record
the plan hashes before execution and preserve every raw attempt and failed log.
No automatic rebaseline is permitted if a patch changes collected IDs.

```bash
python int05_tests/check_full_comparison.py \
  --policy verification/execution-policy.json \
  --affected-plan /external/affected-plan.json --affected-hash sha256:EXPECTED \
  --full-plan /external/full-plan.json --full-hash sha256:EXPECTED \
  --affected-evidence /external/affected --affected-run affected-run-001 \
  --full-evidence /external/full --full-run full-run-001 \
  --expected-owner marketdata-provider --expected-owner optimizer \
  --expected-owner openpine --output /external/comparison-001.json
```

This read-only check delegates raw receipts, phase completeness, source/run
binding, JUnit and failed/missing shard validation to the existing aggregator.
It also checks full owner coverage, exact selected task hashes, independent
scenario scope and preserved owner gates. A green affected run paired with a
failed or incomplete full run returns failure and blocks selection trust.
Passing pytest comparison never grants full stage/release acceptance. Two
small synthetic comparator tests demonstrate the positive and omitted-failure
guards; they do not qualify the eight-owner stack.

Parent review and release of the heavy slot are required before actual complete
campaigns. Hosted run `37526101214` belongs to the integrator; this package
does not launch or cancel hosted runs. Full builds, final candidate acceptance,
provider live cases and the complete 68-requirement spec remain open. No merge,
release or paid resource action is authorized by this checkpoint.
