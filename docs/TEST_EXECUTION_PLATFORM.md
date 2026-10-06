# RC6: source-bound test execution

This is a test-execution layer inside `openpine.verification`, not a second
language implementation or a replacement for foundation/language/release owners.
The stabilization stage is not accepted merely because these commands succeed.

## Commands

Run from the OpenPine repository, with the seven sibling repositories in its
parent directory. Use prepared Python environments with the declared project
extras. All evidence and plans must be outside the eight repository roots.

```bash
python -m openpine.verification test-preflight \
  --host-root . --stack-root .. \
  --python py313=/absolute/path/to/python3.13 \
  --output ../evidence/preflight.json

python -m openpine.verification test-collect \
  --host-root . --stack-root .. \
  --python py313=/absolute/path/to/python3.13 \
  --component pinelib --component openpine-contracts \
  --output ../evidence/collection.json

python -m openpine.verification test-plan \
  --collection ../evidence/collection.json \
  --policy verification/execution-policy.json \
  --profile component --component pinelib --component openpine-contracts \
  --shards 4 --output ../evidence/plan.json

PLAN_HASH=$(python -c 'import json; print(json.load(open("../evidence/plan.json"))["content_hash"])')
python -m openpine.verification test-run \
  --plan ../evidence/plan.json --expected-plan-hash "$PLAN_HASH" \
  --jobs 4 --run-id local-check-001 --output ../evidence/run-001

python -m openpine.verification test-aggregate \
  --plan ../evidence/plan.json --expected-plan-hash "$PLAN_HASH" \
  --evidence ../evidence/run-001 --run-id local-check-001 \
  --output ../evidence/rechecked.json
```

A component profile means **the selected components on the supplied, observed
interpreters**, not certification of every supported Python. `stage-full` and
`release-full` require ordinary CPython 3.13 (`>=3.13,<3.14`) with the GIL enabled.
The current support decision supersedes the earlier 3.11/3.12/3.13 matrix; historical
receipts remain historical. Every functional, coverage and fault obligation remains
required on 3.13. Aliases, PyPy and free-threaded builds cannot satisfy this policy.

`test-preflight --level pytest` checks only the runner environment. Full preflight
also reports declared dependencies, missing Python versions, the Node version
and worker prerequisites. A successful Bubblewrap smoke, where available, is
not a protected-worker protocol/job receipt. Full worker validation remains with
the existing worker owner and is never replaced by in-process execution.

## Profiles and selection

`smoke` uses explicit patterns against an already reviewed complete collection.
`component` executes complete selected component inventories. `affected` uses a
conservative transitive owner/consumer/prerequisite graph. Unknown changes and
shared schemas, catalogs, ABI, state, configuration or verifier changes escalate
to the complete set. There is no unvalidated fine-grained impact pruning.
`integration`, `stage-full` and `release-full` retain their non-pytest obligations.
A green pytest part is reported separately from these owner gates.

The authoritative component scopes are in `verification/execution-policy.json`.
Collection uses the existing `verification/inventory.json`; the planner cannot
rebaseline it. The provider's existing `not live_network` boundary is preserved.
The host still includes `rc6_tests` plus the complete existing
`selected_regressions.json`. File-level shards keep module fixtures together.
Measured duration maps can be supplied to `test-plan --durations`; missing
measurements use deterministic weights, not a claim of balanced measured runtime.

## Evidence and rejection rules

The plan freezes candidate input bytes, reviewed collection, interpreter and
installed distribution versions, component, execution path, variant and exact
node-to-shard assignment. Every process receives a private home/temp/cache and
explicit plugins; inherited pytest options, unrelated PYTHONPATH and credentials
are not forwarded. CPU slots and exclusive groups bound concurrency. RAM is
sampled, not a hard memory limit; full memory-budget qualification is still a
separate stabilization obligation.

The pytest gate rejects an invalid collection **before** executing test bodies.
It records setup/call/teardown, fixture setup counts/durations, collection time,
CPU and process RSS. It rejects skip/xfail, missing or duplicate phases and reports
from unassigned tests. JUnit carries an exact `openpine.nodeid` property; class
name reconstruction or pass-count arithmetic is not used as execution proof.

The aggregator requires every planned shard, matching run/attempt/source/Python
identity, matching file hashes, exact phase reports and exact successful JUnit.
Empty or stale XML, changed source, crashes, timeouts, missing results and repeated
shards cannot silently turn green. A real failed negative-fixture suite is kept
as evidence and its **outer verifier regression** asserts that it is rejected.
No product tests have been marked skipped or expected-failure to pass a gate.

Writes are create-once; use a new run/output directory for another attempt.
There are no hidden retries. Existing failures remain on disk. Timeouts signal
the launched process group and wait for the root process; they do not prove that
the group is empty. A group still present after a nominally successful pytest
exit makes that shard unsuccessful. This is **not** a closed
process-containment boundary: a descendant that detaches into another session
can escape both group cleanup and sampled RSS accounting. Protected-worker
containment and adversarial detached-descendant tests remain separate required
qualification; the RSS budget is observational, not a kernel hard limit.
Receipts are integrity evidence on trusted storage, not cryptographic proof
against an actor who can rewrite all files and their hashes. Installed
distribution version fingerprints are not wheel-only package integrity
certification.

## Existing foundation integration

```bash
python -m openpine.verification test-export-suites \
  --plan ../evidence/plan.json --expected-plan-hash "$PLAN_HASH" \
  --evidence ../evidence/run-001 --run-id local-check-001 \
  --output ../evidence/suite-export
```

Only a verified **complete** component can be exported. Smoke/subset results are
rejected. The export retains the existing inventory receipt shape and adds
plan/source/environment provenance. It does not fabricate `source-pins.json`,
corpus observations, worker receipts, package checks or a Stage 1 verdict.
The existing `run_stage_gate()` must still validate those inputs. Non-pytest
owner gates are explicitly reported as outstanding; this implementation does
not self-authorize stage or release acceptance.

## Fixture optimization

The catalog test module shares an immutable serialized observation, then decodes
a fresh nested graph for every test. Runtime/session/heap/broker state is not
shared. Authority mutation scenarios still call the real generator with their
own modified inputs. New regressions verify mutation isolation and repeatable
fresh generation. The archived test IDs are preserved; the historical-baseline
assertion is adapted for the newer pinned pine2ast release.

`stage2-1-catalog` and `stage2-1-lock` use the restored Stage 2.1 owner and
archived authority. `rc6_tests/test_rc6_stage2_audit_gate_mutations.py` retains
the archived fail-closed audit obligations. The archived frozen catalog baseline
is unchanged and still reports its mismatch against newer RC6 packs. A separate
sealed `rc6_catalog_source_pin.json`, derived from pine2ast Git commit
`ddb164a8819d889150378a303b0d3255cf0b5102`, checks the installed pack
bytes. Review against the archive found metadata changes in v1-v3 and v5-v6,
and the removal of the three `array.binary_search*` function rows in v4.
These exact RC6 source checks replace the obsolete archive baseline as the
*current* integrity gate; the historical mismatch stays visible in the report.
Neither the source pin nor green catalog tests imply Stage 2 acceptance:
unverified contract dimensions and TradingView authority remain open.

## Current limits

The coordinated CI adapter now prepares a source-bound eight-package bundle,
restores it in component jobs, and joins raw fragment/coverage receipts into
its existing foundation gate. The graph retains the mandatory builtin replay,
protected-worker owner, frontend, and ordinary CPython 3.13/GIL lane. Third-party
build and runtime wheels are selected from reviewed SHA-256 requirement locks;
restore checks the bundle hashes and installs offline. These implementation and
contract tests are **not** a completed hosted run or an accepted Stage 1.

The lifecycle source pins now name the seven current RC6 sibling heads, but
historical Stage 2 receipts are intentionally not repinned or treated as proof
for those newer trees. Exact-source collection, complete functional/coverage/
worker/packaging gates on the new candidate, five equivalent whole-stage
performance comparisons, and independent final acceptance remain outstanding.
The supplemental platform workflow checks the verifier itself; it does not
replace any mandatory native or language gate.
