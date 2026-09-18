> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# OpenPine RC6 — Stage 2.3 Package Acceptance

Date: 2026-09-17

## Verdict

**Stage 2.3 — UDF parameters, defaults, scopes and callsite state: ACCEPTED as a package gate.**

The 2026-09-16 v3 receipt remains the semantic baseline. This receipt closes the remaining acceptance blocker from that re-audit: complete post-fix execution of the Pine2AST and Ast2Python inventories.

This does **not** set `full_stage2_accepted=true`.

## Closed blocker

v3 required a complete run of all then-current 3299 Pine2AST tests after the final guard fix. The current locked inventory on this tree is 3336 Pine2AST tests and 1605 Ast2Python tests (inventory grew with Stage 2.4; no shrink).

Executed on CPython 3.12.3 after installing the declared package toolchain (`setuptools>=77`, PyPA `build`) and the `pine2ast` runtime dependency for isolated compiler-boundary tests:

- Pine2AST: 3336/3336 PASS
- Ast2Python: 1605/1605 PASS
- Stage 2.3 Ast2Python UDF execution subset remains included in the 1605

## Retained v3 evidence

- historical whole-pack guards 103/103
- Pine2AST Stage 2.3 targeted 176/176
- Ast2Python Stage 2.3 execution 25/25
- Stage 2.1 catalog regression 11/11
- Stage 2.2 PineLib regression 39/39

## Explicitly not claimed

Python 3.11/3.13 dual matrix, protected workers, frontend/package release gate, TradingView oracle.

`stage2_3_accepted = true`
`full_stage2_accepted = false`
