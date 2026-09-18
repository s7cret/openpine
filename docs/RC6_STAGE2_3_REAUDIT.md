> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# RC6 Stage 2.3 — re-audit 2026-09-16

Status: **CORRECTED CANDIDATE — FULL REGRESSION PENDING**.

The previous `ACCEPTED` receipt is revoked by this re-audit.

## Regression found

`f()=>plot(close)` and the equivalent single-expression user-method path were admitted because builtin local-call validation checked only `local_depth`. Single-expression callable bodies live in `FUNCTION` / `METHOD` lexical scopes without incrementing `local_depth`.

## Fix

`forbidden_in_local_blocks` admission now fails when either a local block is active **or** the lexical scope stack contains `FUNCTION` / `METHOD`. This preserves allowed drawing/runtime calls such as `label.new()` and TA calls such as `ta.sma()`.

## Evidence

- Stage 2.3 UDF scope re-audit: 36/36 PASS.
- Pine2AST collection: 3274 tests, no shrink from 3255.
- Catalog `--check`: PASS.
- PineLib ABI `--check`: PASS.
- Full Pine2AST regression attempt after the fix: incomplete due process execution limit; **not counted as PASS**.

Therefore this archive is intentionally not labelled Final Accepted.
