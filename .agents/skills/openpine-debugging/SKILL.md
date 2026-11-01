---
name: openpine-debugging
description: Diagnose OpenPine defects, failed tests, checkpoint or realtime divergence, resource problems and Windows/Linux differences. Use for a concrete failure before changing its cause.
license: MIT
metadata:
  origin: Adapted from obra/superpowers systematic-debugging
  upstream-commit: 2ce8aa15d05bd57213e6f746f953aafb7ded7a8b
---

# OpenPine debugging

Work from a reproducible symptom to a bounded correction. Preserve a useful
failure artifact before changing code or rerunning a failed CI job.

Resolve the OpenPine Git root before running tools. This workspace contains it
in `openpine-r6/`; a junction to this skill is not a second checkout.

## Locate the failure

Record the input or case ID, command, Python/dependency identity, source commit
and fingerprint, execution model, platform, first meaningful error and exit code.
Read existing complete logs at their recorded paths; truncation in the chat is
not a reason to rerun a suite. Keep large inputs and logs outside tracked source,
in a task-specific directory under `.runtime/` or the workspace proof directory.

Find the smallest reproduction. Compare with a working neighboring test and the
last relevant diff. Trace the first differing value across the applicable chain:

`Pine source → Binder/IR → runtime → execution model → storage/checkpoint → API/replay`.

Inspect only the boundaries involved. For state failures, compare native state
and resumed state at the same event cut, not merely the final chart. Check graph
IDs, alias edges, physical cell/string accounting, var/varip and preview rollback.
For OS differences, check paths/case, file lifecycle, process startup, output order
and dependency identity before changing semantic expectations.

## Test a hypothesis

State a specific cause and the evidence supporting it. Change one variable or
add narrowly scoped diagnostics that can distinguish it from another cause.
Do not dump credentials or entire environments to collect evidence.

Separate a program mismatch from infrastructure delay, a running worker, stale
reports or a stale wheel. A timeout alone does not establish a semantic defect.
Read heartbeat/progress and resource evidence before considering a timeout change.

If a hypothesis fails, retain its result and revise the hypothesis. Repeated
failures justify revisiting the shared invariant or architecture; they do not
by themselves prove that the architecture is wrong or require a new approval.

## Correct and check

Fix the cause in its owning layer. Add a regression that discriminates the
defect when executable behavior changes; use the existing unittest runner and
independent conformance expectations. Prefer a failure reproduction before the
fix. If demonstrating the old failure afterward, use an isolated copy or
worktree and preserve the user's working changes.

Run the reproduction, then relevant neighboring checks. Expand to the R6 gate
when required by the change, not for every diagnostic step. Do not weaken quotas,
rewrite expected results or silently add skips to make the run green.

Report the cause, correction, evidence and remaining uncertainty.
For qualification commands use `openpine-acceptance`; the debugging procedure
itself does not require other skills, a worktree, subagents or network access.

Source: [pinned upstream procedure](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/systematic-debugging/SKILL.md).
The project adaptation record is `docs/AGENT_SKILLS.md` under the Git root.
The upstream MIT notice is retained in [LICENSE](LICENSE).
