> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# OpenPine RC6 — Stage 2.2 Acceptance

Date: 2026-09-16.

## Status

**Stage 2.2 — Bool / NA / numeric conversions / history: ACCEPTED as a local package gate.**

This does **not** mark full Stage 2 accepted. Packages 2.3–2.9 and the coordinated 2.10 release gate remain open. GitHub was not modified.

## Implemented semantic fixes

- Removed Python truthiness fallback from Pine bool coercion. Strings, collections, objects and transport `None` fail closed.
- Preserved version-exact bool semantics: v1–v5 implicit numeric conditions remain supported; v6 requires explicit `bool()`.
- Implemented exact `bool()` and `int()` runtime ABI bindings. `int(float)` truncates toward zero; Python `bool` is not silently admitted as an integer.
- Separated transport `None`, storage-level missing history and the Pine `na` marker.
- Fixed equality so Python's `False == 0` rule cannot leak into Pine semantics.
- Added dynamic-history offset type validation and explicit history limits (5000 ordinary series; 10000 OHLC/time series).
- Added transactional history reservation using the existing series state owner, including commit, abort, checkpoint and restore.
- Lazy/unexecuted branches do not reserve history.
- Preserved Stage 2.1 frozen evidence through an explicit Stage 2.2 manifest delta lock rather than rewriting historical hashes.

## Verification

- PineLib: **5011/5011 PASS** across four disjoint chunks covering the complete current inventory.
- PineLib Stage 2.2 targeted/regression gate: **75/75 PASS**.
- Pine2AST Stage 2.2/catalog/version targeted gate: **17/17 PASS**.
- Ast2Python exact generated-path Stage 2.2 gate: **3/3 PASS**.
- Pine2AST catalog generator `--check`: PASS.
- PineLib ABI manifest `build --check`: PASS.
- Inventory counts increased from Stage 2.1 and did not shrink: PineLib 4978→5011, Pine2AST 3232→3238, Ast2Python 1554→1557.

A monolithic full Ast2Python/Pine2AST run is not claimed as current package evidence; those complete coordinated suites remain part of the Stage 2.10 release gate. This receipt therefore claims only the 2.2 semantic package boundary.
