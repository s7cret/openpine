> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# OpenPine RC6 — Stage 2.4 Reference Lifecycle Acceptance

Date: 2026-09-17

## Verdict

**Stage 2.4 — arrays/maps/matrices and reference lifecycle: ACCEPTED as a package gate.**

The 2026-09-16 semantic implementation receipt remains the behavioural baseline. This receipt closes the remaining acceptance blocker: complete post-change execution of all 3336 Pine2AST and 1605 Ast2Python tests.

This does **not** set `full_stage2_accepted=true`.

## Implemented surface (unchanged)

- typed arrays/maps/matrices on the direct runtime boundary and compiler path
- illegal direct nested collection IDs fail closed
- aliases, shallow copies, shared legal reference elements and history keep identity
- rollback/commit/checkpoint restore the shared graph
- var/varip retention stays local to the binding/field policy
- Pine v6 bool collection initialization is version-correct

## Verification

- Pine2AST complete inventory: 3336/3336 PASS (CPython 3.12.3)
- Ast2Python complete inventory: 1605/1605 PASS (CPython 3.12.3)
- Prior PineLib complete inventory on this tree: 5030/5030 PASS
- Stage 2.4 targeted contracts remain included in those inventories (Pine2AST 37, Ast2Python 23, PineLib 19)

## Status

`stage2_4_semantic_gate = true`
`stage2_4_accepted = true`
`full_stage2_accepted = false`

Python 3.11/3.13, protected workers and Stage 2.10 coordinated gates are not claimed.
