---
name: openpine-acceptance
description: Run or audit OpenPine R6 qualification, source-bound CI receipts, shared wheel and recovery evidence, and publish an accurate coverage record. Use for acceptance work, not a plan request or routine docs edit.
license: MIT
---

# OpenPine R6 acceptance

Use the repository's qualification tools as the authority. This skill coordinates
them; it is not another release gate and does not alter their invariants.

Locate the OpenPine Git root first: `openpine-r6/` under this workspace.
Run project tools and resolve repository paths there, not in the parent folder
or a directory junction exposing this skill.

## Select the requested level

- Targeted check: reproduction, affected unittest module and relevant
  specification/architecture checks. Report this scope explicitly.
- Fast feedback: `tools.release_gate --fast`. This never qualifies a release.
- Full local gate: `tools.release_gate`. This proves the local platform and
  native execution only; it does not replace installed-wheel, second-OS or
  recovery qualification.
- Full CI acceptance: the current workflow plus independent receipt audit and
  relevant change-specific evidence.

Skills/docs-only changes normally need structural and behavioral review, not a
new full application run. Compare the application fingerprint and clearly
identify any earlier application evidence reused.

Read [commands and qualification order](references/commands.md) only for the
requested level. Read [evidence and coverage](references/evidence.md) for full
acceptance or publication.

## Bind the evidence

Confirm the repository root/branch and Python **3.14.8**, then capture
`tools.ci_artifacts.source_identity()` before running qualification. Keep source
and dependencies stable while checks run.

Inventory test IDs, conformance cases, profiles and whole-program examples from
current code and the previous accepted baseline. Counts in an older acceptance
document are observations, not constants to copy forward.

Complete and review implementation, fixtures and generated reports before
pushing executable changes. Regenerate reports against that source; do not
commit stale reports while waiting for CI. Preserve immutable imported
specification bundles. Availability and explicit rejection in Pine v1–v6 must
remain visible.

Build one shared wheel and use that artifact for installed checks and recovery.
Do not combine different candidates or wheels. Preserve the required platform,
suite and shard identity of each receipt. Successful jobs retained from an
earlier attempt of the same run may be reused when the identities still match
and the latest required job results pass. Use the existing audit to reject
missing/duplicate/stale evidence.
Reusing completed evidence is valid only when the relevant fingerprint,
environment and audited artifact contents still match.

## Resolve and publish

Diagnose the first meaningful failure. Read complete saved logs rather than
re-running to regenerate unread evidence. For an infrastructure retry, preserve
the original failure, explain the cause, and retry only the affected job and its
dependents when feasible. Keep source, wheel and limits unchanged; another
identical failure calls for diagnosis, not an unbounded retry loop.

Publish only conclusions supported by completed checks. Record skips, expected
rejects, model differences, source interpretations and unverified external
equivalence explicitly. Update the semantic matrix and stage acceptance record
with stable source/CI links. Check the final Git diff and remote branch if a push
is authorized by the task. Do not edit published code during its qualification.

Keep substantial raw logs, wheels and downloaded artifacts in the task proof
directory. Commit concise provenance and reviewed reports in the existing
repository conventions. Documentation can link the qualified source commit;
do not claim that its own later commit ran the earlier CI.

The local Pine Wiki is outside this repository. Update it only when covered by
the user's task or standing request; retain its earlier text and do not push it
unless that action is authorized. This skill does not require a Wiki checkout.

This skill contains no auto-install hooks, automatic agent delegation or new
publication approval. Follow the current task's actual authorization.
