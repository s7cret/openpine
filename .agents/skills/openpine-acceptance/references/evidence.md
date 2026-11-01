# Evidence and coverage

## Before/after retention

Choose the previous accepted baseline from Git and its linked evidence, not an
unreviewed worktree or a guessed count. Record its commit and the new input
fingerprint. Derive inventories from actual test IDs, conformance case IDs and
whole-program corpus records. Useful entry points are:

- `tools.ci_artifacts.expected_cases()`, `modules()` and
  `conformance_inventory()`.
- `conformance/*/cases.json`, committed `conformance/results/*.json`,
  `examples/corpus/manifest.json` and their acceptance records.
- `spec/lock.json`, its pinned bundle and existing profile documents.
- `docs/SEMANTIC_COVERAGE.md` and the relevant feature guide/acceptance document.

Keep previous test/case IDs and immutable bundles. Investigate every deletion
and expectation change; an intentional support transition needs explicit IDs,
old/new observations, version boundaries and a reason. A larger aggregate count
does not establish retention. Preserve corpus records including comparisons and
explicit model differences, rather than checking only their number.

Compare portable observations between platforms. Exclude only fields whose
platform dependence or changed identity is justified and documented. Do not
silently normalize away output order, errors, qualifiers, state or aliases.

## Source and artifact identity

Keep these identities separate:

1. Qualified source commit and complete application fingerprint/environment.
2. Actual workflow run, attempt(s), jobs and their conclusions.
3. Downloaded artifact IDs and verified archive digests.
4. One shared wheel SHA and its source-bound manifest.
5. Independently audited native/specification/installed/recovery receipts.
6. Later documentation/publication commit, if different.

Derive expected jobs from the current workflow and receipt inventories. Avoid
hardcoded totals from a previous stage. Verify archive digests and safe
extraction paths; do not send Git credentials to an artifact's redirected host.
Prefer the connected GitHub tools for supported reads.

On a rerun, preserve the failed attempt and select the latest job for each
required name. Explain whether the original failure was a mismatch or an
execution/infrastructure limit, and the evidence for that diagnosis. A retry on
changed code or wheel is a new candidate, not the same qualification.

## Feature-specific proof

For a semantic change, retain small independently expected programs for every
supported version and expected rejects for unsupported syntax. Exercise the
consumer chain involved by that feature: saved IR, native execution, worker,
CLI/HTTP, historical/realtime replay and durable resume as applicable.

For shared mutable state, compare native state around event cuts as well as
observable outputs; cover aliases, IDs, rollback, cancellation and quota
atomicity. For storage changes, include restart/range-read and recovery paths.
Reuse existing fixtures/helpers instead of creating a second testing framework.
Select meaningful cases from the actual change; this is not a checklist forcing
every subsystem to run for a documentation edit.

Internal conformance establishes the bounded implementation profile. Keep
external TradingView execution comparison as a separate evidence category.

## Publish a concise record

Follow the existing feature acceptance conventions in `docs/` and
`docs/evidence/`. Record the qualified source, fingerprint, Python, workflow
URL/attempt, actual counts, skips/rejections/model differences and material
limitations. Link raw evidence rather than committing all transient logs or
wheels. Update `docs/SEMANTIC_COVERAGE.md` from completed evidence.

If a lasting architecture/semantic interpretation changed, record why in an
ADR within the authorized task. Do not reopen settled user decisions merely to
write that record.

Check the final source fingerprint again before claiming acceptance. A docs-only
follow-up may reuse unchanged application evidence, but must not imply that old
CI evaluated the new instructions or the new docs payload in a rebuilt wheel.
