# ADR: one plan, portable locators and raw owner evidence

## Problem
The local executor had exact single-machine shards but no coherent transfer and
aggregation contract across runner locations. Reusing summaries could conceal a
missing raw attempt. Affinity alone overstated available CPU under a cgroup quota.

## Decision and ownership
Extend the existing `openpine.verification` planner, pytest gate and campaign
reader. Immutable plans retain source/tests/expected/environment identities.
A separately sealed locator maps physical roots and interpreter paths without
changing semantic identity. Fragments contain the raw phase/JUnit/command evidence
and primary attempts; aggregation proves their exact planned union and rechecks
provenance. Original failure attempts are retained; no implicit retry exists.

Coverage remains with Coverage.py and each component's declared configuration;
foundation remains with `stage_gate.run_stage_gate`. CI adapts these owners rather
than creating another acceptance flag. Collected performance markers split traced
and untraced execution while preserving every required node.

CPU budget uses affinity and inherited cgroup quota. Optional memory reservations
and RSS sampling reject known over-budget or unobservable execution, but are not
kernel containment. Protected product workers remain a distinct mandatory owner.
The optimizer's actual kernel prerequisite is probed before its long test suite;
absence of its descendant interface is not papered over by an empty process list.

## Compatibility
Existing single-host v1 inventory exports remain readable. New optional plan
marker/binding fields require matching readers for new plans; old receipts remain
historical evidence only for their recorded candidate. Artifact integrity is not
an authenticity signature against wholesale rewriting of the evidence store.
Cross-runner elapsed cost is never labelled measured parallel wall time.

## Alternatives rejected
Changing hashes to relocate a plan; accepting per-run summary counters alone;
removing slow nodes, lowering coverage, substituting fake sandbox/NA semantics;
a second verifier with independently writable full-stage flags.

## Validation and limitations
Positive transfer, exact union, wrong-source/environment/attempt, duplicate/missing
shards, malformed/tampered XML, phase omissions, timeout/crash and isolated mutable
fixture tests are in the execution test modules. Coverage tests exercise both a
real 100% case and a failing unchanged threshold. Budget tests cover inherited
v1/v2 quotas and denied memory observations. Actual complete RC6 CI and current
remote source reconciliation are not implied by these tests.
