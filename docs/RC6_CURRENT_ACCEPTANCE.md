# RC6 current acceptance: one owner, raw replay

`stage_gate.run_stabilization_gate` extends the existing foundation owner; it
neither renames the foundation receipt nor accepts the complete Pine language.
The canonical produced contract is `openpine.rc6_current_acceptance.v1`.
`test-current --view current|progress|remainder|summary` always rebuilds that
contract from primary evidence before rendering a view. A saved report is only
checked for equality with the rebuilt contract; its seal is not authority.
`current_views` is a rendering/consistency helper, **not** an admission API.

The checkout's `verification/stage2-current-acceptance.json` is the initial
`not_run` reference, not a receipt. Publish the generated current contract in an
external immutable evidence directory. Do not put generated receipts into source
roots or inject their plan hashes into source inputs (that is a circular identity).
`verification/stage2-progress.json` now points to this contract. The previous
progress document is preserved byte-for-byte under `verification/history/`;
its SHAs, CI run IDs and counts have not been relabelled as current execution.
The original `stage2-remaining` command still verifies its narrower builtin
index/matrix scope; its report identifies the canonical current reader explicitly.

## Commands for the integrator

Freeze source, inventory, execution policy including the raw owner specifications,
and all observed interpreters before generating the qualified `stage-full` plan.
Use the existing `test-ci plan`/campaign/merge/export/coverage owners. The current
reader requires `source_commits` for all eight owners, matching lifecycle pins,
all reviewed component inventories, all policy Python obligations (including
library 3.12 obligations), and exactly the seven named owner gates.

```sh
python -m openpine.verification test-stabilization \
  --host-root "$HOST" --plan "$PLAN" --expected-plan-hash "$PLAN_HASH" \
  --evidence "$EVIDENCE" --run-id "$RUN_ID" \
  --output "$EVIDENCE/rc6-current.json"

python -m openpine.verification test-current \
  --host-root "$HOST" --plan "$PLAN" --expected-plan-hash "$PLAN_HASH" \
  --evidence "$EVIDENCE" --run-id "$RUN_ID" \
  --saved-current "$EVIDENCE/rc6-current.json" --view remainder \
  --output "$EVIDENCE/current-remainder.json"
```

Both commands return 0 **only** for accepted stabilization. Missing gate evidence
or a missing reviewed raw-reader specification is `not_run`; invalid primary
materials are `blocked`. Stale source/plan/lifecycle binding is rejected. Stage 2
is independently `in_progress`, `full_stage2_accepted=false`, with the actual
locked matrix items and criteria. Full release acceptance is never inferred.

## Locator packet

Write `$EVIDENCE/stabilization-inputs.json` after primary execution. It is a
locator, not a PASS receipt. Paths are relative to `$EVIDENCE`; directory paths
must be real directories, and symlinks/traversal are forbidden.

```json
{
  "schema_id": "openpine.rc6_stabilization_inputs.v1",
  "plan_hash": "sha256:<qualified plan hash>",
  "candidate_hash": "sha256:<execution source snapshot hash>",
  "run_id": "<actual merged campaign id>",
  "campaign": "campaign",
  "gates": {
    "branch-reconciliation": {},
    "foundation": {"environments": {"py311": "foundation/py311", "py313": "foundation/py313"}},
    "protected-workers": {},
    "coverage": {"tasks": {"openpine@py311": "coverage/openpine@py311"}},
    "frontend": {"commands": [], "tests": {}, "outputs": []},
    "packages": {"3.11": {}, "3.12": {}, "3.13": {}},
    "test-performance": {"before": [], "after": []}
  }
}
```

The abbreviated coverage map must actually enumerate **every instrumented task**;
coverage admission requires complete full-component execution, the unchanged
owner config, three successful combine/json/report commands with raw logs,
recomputed JSON totals/files/branches, and a combined database equal to the
hash-checked shard database union. This works for complete multi-component
campaigns as well as existing single-task fragments.

Foundation directories are the corresponding existing `test-export-suites`
interpreter directories with `source-pins.json`, actual corpus observations and
`stage1.json` produced by the existing `stage1` command. The reader reruns the
foundation owner without writing its evidence, and checks the exports' actual
aggregate/candidate/plan/run/environment bindings. Only environments with all
eight component tasks need a full foundation receipt; additional library-only
3.12 tasks still remain mandatory campaign/coverage/package obligations.

## Frozen raw-reader policy

The existing `verification/execution-policy.json` gains a `stabilization` object.
Its specifications are source inputs and are anchored by the externally supplied
qualified plan hash. Missing specifications remain open, not implicitly passed.
Do not add arbitrary caller-provided expected values to the evidence packet.
The current production policy intentionally cannot accept missing raw owners.
Integrators must supply reviewed concrete specifications **before** freezing the
final candidate; old package/performance artifacts are not current evidence.

* `branch-reconciliation`: `register`, `sha256`, `required_ids`. The machine
  register contains `rows` with exact ordered IDs, decision, reason, and
  `mappings` (`component`, source-relative `path`, `sha256`, optional `nodes`).
  `port_required` blocks acceptance. Useful decisions need mappings whose actual
  candidate bytes and executed tests resolve. Rejected decisions need reasons.
  This is an adapter for the integrator's reviewed decision register, not another
  PR evaluator; the reviewed denominator must include every residual obligation.
* `foundation`: `stack_root`. Its actual eight-owner source snapshot must match
  the campaign candidate; the original owner still verifies architecture,
  capability policies and the frozen independent conformance corpus.
* `protected-workers`: `nodes` maps components to reviewed explicit protected
  worker test node IDs. These must exist in every corresponding executed
  interpreter task. The reader revalidates all phases/JUnit, not a worker summary.
* `coverage`: `{}` uses unchanged existing per-owner policies and raw databases.
* `frontend`: `commands` (exact `argv`, absolute `cwd`, optional independently
  specified `expected_stdout` JSON), `test_names` (exact Vitest/Jest assertion
  `fullName` inventory), `build_output_count`. Packet command descriptors point
  to real `run_logged` `command.json`; `tests` is a hash descriptor for raw
  Vitest/Jest JSON, `outputs` are nonempty build artifact hash descriptors.
* `packages`: mandatory Python-version keys each specify `python`, `commands`,
  `resources` (component to required wheel-member paths). Packet entries contain
  corresponding `commands`, `probe`, `artifacts` (all eight components with
  `wheel` and `sdist` hash descriptors). Command specifications carry roles:
  `wheel`, `sdist`, `sdist-wheel`, `install`, `probe`, `compile`, `run`, `library`,
  `resources`, `missing-resource`, `tampered-resource`, `source-shadowing`.
  Concrete independently reviewed semantic/error outputs are required for the
  latter seven roles; generic `ok`/`passed`/`status` objects cannot substitute.
  Use the actual isolated probe stdout as the `probe` descriptor:
  `INSTALLED_PYTHON -I -m openpine.verification.installed_probe`, with no
  `PYTHONPATH` and cwd outside source roots. The reader verifies actual Python
  binary/version, prefix and noneditable origins, all eight active lock versions,
  installed file hashes against wheel bytes, wheel/sdist source bytes against
  the candidate, and required installed resources. It does not read resources
  from a neighboring checkout. Keep the installed prefixes available for replay.
* `test-performance`: `reference_profile`, `target_speedup` (production target
  2.0). Each packet before/after row contains a plan hash descriptor, campaign
  directory, actual `run_id`. At least five distinct valid raw campaigns in
  each organization must match the current full workload/resource budget. The
  existing `compare_campaigns` owner is called; duplicate/stale/failed samples
  and an unmet target block the current gate. Retain extended phase/critical-path
  and pipeline-cost evidence required by FIX-07; this adapter does not invent
  unavailable build/install/queue/upload timing from pytest wall time.

## Verifier fixture versus actual qualification

`rc6_tests/test_rc6_stabilization.py` constructs an explicit miniature eight-owner
fixture: actual wheel+sdist builds, sdist-to-wheel builds, fresh isolated install,
installed resource/error/shadowing probes, a JS assertion/build, ten real bounded
four-process campaigns, JUnit/phases/coverage, and the existing foundation owner.
This proves the reader's full positive path and tamper/stale/missing rejection.
The fixture's independently authored arithmetic expectations, zero coverage
minimum and near-zero timing target apply **only to that synthetic fixture**.
They do not change production policies, certify Pine semantics, demonstrate 2x
product speedup or accept the current RC6 candidate.
