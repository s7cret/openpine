# INT05: qualified profile boundaries, parent review checkpoint

Historical initial-base evidence follows. For the current integration candidate,
negative reader, actual producer routing and mandatory 20 GiB free-disk floor,
use [INT05_NEGATIVE_READER_REVIEW.md](INT05_NEGATIVE_READER_REVIEW.md). Its execution
design and budget supersede the forecasts below; no complete qualification has
been executed.

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

The full-inventory hash guard checks structural consistency against the hash
already recorded in the plan. It does not authenticate a caller who fabricates
a smaller inventory and rewrites every hash before resealing. Authenticity
depends on the independently reviewed collection and externally frozen expected
plan hash. An explicit coordinated-rehash test demonstrates that the forged
self-consistent structure is rejected only when the trusted plan anchor is
supplied. Acceptance readers must keep that anchor separate from input files.

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

The initial targeted regression passed 153 tests in 14.42 seconds; one existing
ten-campaign performance self-test was explicitly left out of this bounded
selection. Lint and diff validation passed. The final ten real patch probes and
eight real smoke checks are reported separately to avoid summing repeated runs.
After review fixes, the complete bounded regression passed 178 tests in 34.57
seconds, including 58 profile/comparator/attestation checks, the ten real mutation
probes, eight smoke checks and existing execution-owner regressions. The same
ten-campaign performance self-test remains excluded. Instrumentation drift was
first reproduced as two failing negative checks, then rejected before evidence
aggregation; ordered plugin and producer-identity drift have separate guards.

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

Use the existing `test-ci` source-attestation owner path before execution.
`test-plan` and `reproduce_profiles.py` produce planning-only structures without
`source_commits`; running an OpenPine shard from them fails with
`exact plan producer commits are required`. They must not be used as execution
plans or have execution hashes recorded before the existing CI attestation
step. For a prepared candidate, `test-ci plan` verifies prepared bundles,
`attest_ci_source_commits` checks all eight producer revisions, and
`make_ci_plan` seals those identities into the full plan. The affected plan must
carry the same owner-attested source commits before its execution hash is
externally frozen. Current `test-plan` CLI cannot inject this attestation; any
shared CLI extension belongs to the integrator.

`int05_tests/test_ci_attestation.py` proves the existing
`attest_ci_source_commits → make_ci_plan → run_campaign` path reaches a real
OpenPine process shard with the exact attested compiler identities and host
build commit, even when the caller environment contains stale producer values.
Its locally committed eight-owner fixture is a small launch contract, not
eight-owner product qualification or a prepared wheelhouse claim.
The affected composition is also launched through the existing `run_campaign`
without an explicit build commit, as `test-run` does: restored Git provenance
supplies the exact host identity and stale caller producer settings are replaced.

After the parent has reviewed the fixes, assembled one exact integration
candidate and freed the heavy pool, prepare that candidate once with the existing
owner. These commands are a recipe; preparation and full execution were not run
for this package:

```bash
python -m openpine.verification test-ci prepare \
  --host-root /candidate/openpine --work /external/ci-prepared-001 \
  --python-version 3.13
python -m openpine.verification test-ci plan \
  --bundle /external/ci-prepared-001/bundle --work /external/ci-plan-001 \
  --owner-locations /external/reviewed-owner-locations.json \
  --output /external/attested-full.json
python -m openpine.verification test-ci owner-environment \
  --bundle /external/ci-prepared-001/bundle --work /external/ci-restored-001
```

Freeze the bundle and full-plan content hashes separately before execution.
Run the affected composition from the candidate host directory, using the
interpreter recorded by the prepared owner:

```bash
python -m int05_tests.affected_from_ci \
  --bundle /external/ci-prepared-001/bundle \
  --full-plan /external/attested-full.json \
  --expected-bundle-hash sha256:REVIEWED_BUNDLE \
  --expected-full-hash sha256:REVIEWED_FULL \
  --changed marketdata-provider/marketdata_provider/core/bar.py \
  --expected-owner marketdata-provider --expected-owner optimizer \
  --expected-owner openpine --output /external/attested-affected.json
```

This small planning adapter calls the existing bundle verifier, attestation
owner, collection joiner and planner. It writes an existing-schema plan only
after source commits, source/environment identity, independently expected owner
scope, selected nodes and instrumentation agree with the full plan. It has no
runner or acceptance logic. Record its execution hash in the external checkpoint.
Use existing `test-bind` to bind each plan to **all eight** `restored.json` roots
and its actual `py313` executable. Run both plans using the restored interpreter
and the existing `test-run`, with the independently frozen expected hash,
the corresponding binding, `--jobs 2 --max-parallel-shards 1 --memory-mib 6144`,
unique output roots and run IDs. The retained restored Git provenance supplies
the host commit; the plan supplies the exact compiler producer commits.
Preparation owners are still present in the binding, without adding them to
the affected test obligations.

Use existing collection, execution and aggregation owners on each real patch
candidate after this attestation. Keep identical frozen source, observed interpreter,
policy, reviewed inventories and instrumentation for its affected/full pair.
Instrumentation comparison includes the coverage package, untraced marker set,
node marker evidence and actual coverage assignment for every node. Shard
grouping may differ only when each obligation retains identical instrumentation.
Ordered plugin settings and `source_commits` must also match, because both can
change execution even when source manifests and interpreter identities match.
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

## Real patch campaign proposed for parent review

The ten deliberately failing mutations are sensitivity probes. Even an affected
run that catches the same failures as a full run is not a green qualification
pair. Keep their negative evidence separate; never reuse the unmutated full run
as evidence for different mutant bytes.

`freeze_real_patches.py` archives the last actual Git edit of each scenario file,
its real parent preimage, current candidate postimage and native Git patch.
It checks clean exact library pins, replays each patch and verifies every oracle
is in the reviewed product inventory. It retains an existing-owner source archive
and eight Git provenance bundles. Before images hold all other current candidate
files constant; they are single-file replay inputs, not historical whole-stack
snapshots. This command performs no test execution or package preparation:

```bash
python -m int05_tests.freeze_real_patches \
  --collection /external/reviewed-candidate-collection.json \
  --output /external/real-patches-attempt-001
```

Proposed positive comparison: one full run on the exact reviewed common postimage
and ten actual affected runs for these real replay transitions. Full evidence may
be reused only when the comparator proves identical source, environment, policy,
producer commits, reviewed node inventories, plugins and per-node instrumentation.
The parent must review this campaign design before execution. A new integration
commit or central policy change invalidates the preview and requires recollection,
patch refreezing, attestation and new externally recorded plan hashes.

At this package's counts this is 254,110 executed obligations across 11 campaigns
(9.756 full-suite equivalents). Separate full runs for all ten postimages plus
one baseline would be 514,580 obligations across 21 campaigns (19.756 equivalents).
These are workload counts, not measured speed. An assumed full-run range of
8–30 minutes gives about 1.3–4.9 hours for the common-postimage comparison, plus
preparation; the first actual full run must replace that forecast. Reserve up to
10 GiB additional disk for one preparation, one restored environment, source
inputs and all raw/coverage artifacts; keep at least 2 GiB free. Checkpoint after
each attempt, make no automatic retries and preserve every failed log and primary.
Only disposable private trees owned by that attempt may be removed.

Functional profile comparison does not replace full owner coverage, foundation,
builtin, frontend, lifecycle, package and final stage/release obligations. Their
existing owner path and policy gates remain required for full acceptance.

Parent review and release of the heavy slot are required before actual complete
campaigns. The parent's latest reservation is hosted run `37529938988` at
`f02dfb6` (target 6,318); this package
does not launch or cancel hosted runs. Full builds, final candidate acceptance,
provider live cases and the complete 68-requirement spec remain open. No merge,
release or paid resource action is authorized by this checkpoint.
