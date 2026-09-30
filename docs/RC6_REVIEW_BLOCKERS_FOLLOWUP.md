# RC6 PR21 review-blocker follow-up

This patch closes admission defects, not the product stabilization campaign.
Current acceptance remains **blocked / in_progress**; full Stage 2 acceptance
remains false. Existing frozen attempts and running services are untouched.

- Performance workload identity includes CPU, memory and exclusive-group
  reservations. Frozen organization limits now specify actual quota, memory,
  address-space and CPU-frequency observations, shard layout and concurrency.
  The reader checks primary attempt intervals for cross-component overlap,
  reservation/exclusive-group violations and the declared parallelism bound.
  Use `test-run --jobs 4 --max-parallel-shards 1` for the serial baseline,
  retaining optimizer's original four-slot reservation; the after organization
  allows at most four shards within one component.
- `run_logged(inputs=...)` captures every frozen helper input into immutable
  receipt-owned bytes and records before/after hashes. The common owner consumes
  the frozen harness input map instead of trusting current helper paths or a
  producer's generic success flag. Changed/deleted inputs fail qualification.
- Frontend primaries carry exact plan/candidate, staged UI file inventory,
  captured source inputs and produced artifact descriptors. Stale UI bytes,
  foreign plans and artifacts unbound to the producer are rejected.
- Each Python version requires separate `normal` and `rebuilt` package entries,
  separate admitted wheel sets and installed prefixes, origin probes and all
  existing semantic/resource obligations. Wheels are tied to actual complete
  stack installation argv. Rebuilt-wheel damage cannot hide behind normal-wheel
  probes. Archive readers bound expanded size/count and reject unsafe/duplicate
  members.
- The installed-wheel API smoke resolves explicit sibling sources through
  `OPENPINE_SOURCE_ROOTS` (JSON component-to-root mapping), with exact sibling
  layout as fallback, and builds disposable copies. Installed module origins
  are never used as buildable source roots. Planned workers export this mapping.

## Producers and fresh evidence

Reproducible helper copies live in `scripts/rc6_stabilization/`. Updated helpers
were also copied into a new, unexecuted `exact-campaign-review-02/tools` attempt;
`exact-campaign-01` is not edited. Before execution, stage all eight reviewed
sources, review/freeze exact command declarations and the updated policy, then
create a **new** plan and candidate identity. Frontend orchestration requires
`OPENPINE_ATTEMPT_ROOT` and `OPENPINE_PLAN`; the package exporter emits both
installation sets. Exported command templates still require independent review,
not automatic adoption from observed stdout. No full campaign was started here.

## Focused verification, not acceptance

The broad Python 3.13 verifier pack produced 236 passes and one historical-pin
assertion failure; the assertion was corrected to preserve the historical review
SHAs rather than rewriting history, and the exact failing test then passed.
A final focused set passed 33 tests on each real Python 3.11/3.12/3.13 interpreter.
Both retained real miniature eight-component fixtures re-read successfully after
the final owner changes, retaining `full_stage2_accepted=false`. The repaired
installed-wheel smoke passed with installed sibling imports and explicit source
mappings. A real producer smoke captured all six pinned helpers and re-read its
primary command successfully. The duplicate-wheel negative intentionally emits
zipfile's duplicate-name warning while the owner rejects the archive.

Final host collection was 11,139 with zero deselections and the unchanged 11,027
hashed baseline on all three interpreters: 27 new review regressions were added
to the pre-existing additive descriptor. ast2python collection is 2,082, retaining
its exact 2,079 hashed baseline and three reviewed measurement additions.

Current source pins use the verified RC6 merge commits:
ast2python `17ad5f4b6f9c59cb549f4eb08a2c926561985f16`,
pine2ast `6dfd47badff5ef5b038dfd48a9fb3d8e762b1836`, and
marketdata-provider `70947ab248db1edb955c7addc9a855834d342883`.
Package tree hashes are derived from source using the existing stack-lock owner,
not tuned to wheel observations. Old source-bound evidence remains historical.
Independent review and fresh exact owner evidence remain required before merge.

## B3 defensive execution-source binding correction

The frontend owner now derives the staged UI root from the frozen test command
cwd and requires the build and all UI consumers to share it. The OpenAPI export
retains its legitimate backend cwd. Captured candidate inputs are checked at
that execution-derived root, not a caller-selected inventory directory. Test
descriptors must name the frozen JSON reporter output, and build descriptors
must cover exactly the executed UI root's complete `dist` inventory; copied
foreign artifacts cannot be admitted merely by matching producer metadata.

Six miniature local regressions cover the positive execution, detached source,
detached capture, split execution cwd, foreign build output and foreign test
output. Four new negatives failed before the correction (missing rejection);
all six then passed. The real miniature seven-owner positive and resealed
frontend-result negative were updated to execute against staged candidate UI
sources and actual reporter/build paths, without weakening their assertions.

Final native Python 3.13 focused verification: **78 passed**, zero skips/xfails,
with the existing intentional duplicate-wheel warning. All 14 stack-lock tests,
including the isolated installed-wheel API smoke, passed with coherent explicit
integration sibling source mappings. Ruff passes on changed
owner/fixture tests; the admission test file retains only its pre-existing
E701/E702/F401/F811 diagnostics. Full `tests rc6_tests` collection is **11,145**
with zero deselections: exactly six additive node IDs, retaining the unchanged
11,027 hashed baseline. The package-tree lock is regenerated from source by the
existing owner. External helper bytes and policy hashes are unchanged. No full
product campaign, acceptance promotion, snapshot/service mutation or merge.
