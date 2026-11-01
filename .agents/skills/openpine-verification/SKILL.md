---
name: openpine-verification
description: Verify OpenPine completion, regression and publication claims against the relevant source and actual check results before a commit or handoff. Use for finished changes, not general discussion.
license: MIT
metadata:
  origin: Adapted from obra/superpowers verification-before-completion
  upstream-commit: 2ce8aa15d05bd57213e6f746f953aafb7ded7a8b
---

# OpenPine verification

For each material claim, identify the evidence that proves it and read that
evidence. Use the smallest sufficient checks, then report their actual scope.

Run project commands from the OpenPine Git root, `openpine-r6/` when the
current workspace is its parent. Resolve repository paths from that root.

## Choose evidence for the change

- Documentation and skills: validate syntax/links or skill structure as relevant,
  inspect the diff and check that instructions and command examples match the
  current tools. Application tests do not prove skill behavior.
- Executable behavior: reproduce the original symptom and run relevant tests;
  use the existing R6 qualification for changes requiring full acceptance.
- Requirements: reconcile the delivered behavior with the accepted task and
  explicit version/support boundaries. Passing tests alone do not prove scope.
- Publication, when authorized: verify the intended branch and pushed commit.
  A successful local commit does not prove a successful push.

A pending, cancelled, skipped or failed required check is not a pass. Preserve
meaningful skips and model differences explicitly instead of folding them into
a success total.

## Reuse valid evidence

An already completed check may be reused when its relevant inputs and
environment are unchanged and its recorded result is available. For R6, use
the repository's content fingerprint and receipt audit, not timestamps or a
matching version label. Read partial/truncated output from its file.

Do not rerun the whole suite on every response, commit or status message.
If inputs changed, rerun the affected checks or qualification. A docs-only
follow-up may reference an earlier qualified application source, but identify
that source; do not claim CI ran on the documentation commit.

The R6 application fingerprint excludes `.agents/`, `AGENTS.md` and docs.
Check those changes separately. For full source/wheel/receipt mechanics, use
`openpine-acceptance` rather than inventing another release gate.

## Finish accurately

Inspect `git diff --check`, the intended diff, untracked files and the working
tree. Keep user changes separate and report any intentional leftovers. If
publication is part of the task, inspect the final remote branch after pushing.

Summarize what changed, the checks that actually ran or were reused, the
qualified source where applicable, and material limitations. Do not claim
TradingView equivalence from internal conformance or declare a new failure
"environmental" without evidence.

This skill adds no approval gate or external-action authorization. Continue
within the user's existing task; it does not require agents or a fixed TDD ritual.

Source: [pinned upstream procedure](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/verification-before-completion/SKILL.md).
The project adaptation record is `docs/AGENT_SKILLS.md` under the Git root.
The upstream MIT notice is retained in [LICENSE](LICENSE).
