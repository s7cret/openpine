> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# OpenPine RC6 — Stage 2.1 Final Acceptance

Date: 2026-09-15

## Verdict

**Stage 2.1 — Pine v1-v6 catalog, qualifiers and inputs: ACCEPTED.**

This verdict is limited to package 2.1. `full_stage2_accepted` remains `false`; Stage 2.9 owns independent builtin behavior/oracle closure, and Stage 2.10 owns the coordinated full-stage release gate.

## Acceptance model

The package uses `EXHAUSTIVE_DENOMINATOR_WITH_EXPLICIT_UNVERIFIED`. Every public version/name/form and every contract dimension stays represented. Lack of independent proof is recorded as `UNVERIFIED`; it is never removed from the denominator or promoted to `RUNTIME_DIRECT` merely because code compiles.

The frozen incoming archive remains a **DRIFT_GUARD_ONLY** and cannot accept itself. Official v5/v6 TradingView reference indexes define the modern public name denominator. Verified TradingView migration/source records bind the historical v1-v4 chain.

## Final catalog evidence

- catalog integrity: PASS
- Stage 2.1 acceptance gate: PASS
- acceptance blockers: 0
- canonical symbol identities: 1538
- version cells: 9228
- public callable forms in contract denominator: 2378
- contract-dimension cells: 11890
- represented dimensions: 11370
- explicit UNVERIFIED dimensions: 520
- silent dimension omissions: 0
- independently locked modern input forms: 44
- external TradingView 1:1 exhaustiveness claimed: **NO**

## Modern public-surface closure

The v5/v6 official name indexes are checked for missing required names. Local extras no longer silently enlarge the public Pine surface. Each extra is classified as one of: compatibility mirror, internal type projection, internal generic specialization, explicit OpenPine Stage-2 extension, rejected legacy spelling, or fail-closed local extra.

The frontend now fails closed for accidental v6 callable/alias admissions found during the final audit, while preserving official generic forms such as `array.new<T>()`, `map.new<K,V>()`, `matrix.new<T>()` and non-generic `array.from()`.

## Tests

- current final targeted Stage 2.1 gate: **60/60 PASS**, 0 failures, 0 errors, 0 skipped
- Pine2AST inventory: **3232** nodeids, unchanged from the re-audit inventory
- catalog generator `--check`: PASS
- previous unchanged-source full Ast2Python and PineLib receipts remain preserved as historical evidence

A fresh whole-Stage-2 release gate is intentionally not substituted for this package-specific acceptance.

## GitHub

GitHub was not modified. Pine2AST PR #12 was reviewed and compatible changes were incorporated only into this local delivery.
