> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# OpenPine RC6 — Stage 2.3 second re-audit

Status: **corrected candidate; not accepted yet**.

The second re-audit found three additional issues beyond the first scope fix:

1. `polyline.new()` was incorrectly rejected in local/UDF scope because a frozen catalog flag contradicted the documented drawing-object contract. The frozen RC5 source is unchanged; the materialized v5/v6 contract now removes only that erroneous flag.
2. Single-expression UDFs/methods could admit global-only script declarations (`indicator`, `strategy`, `library`, and legacy `study`/`strategy`). Call-expression admission now enforces `scope=global_only` in FUNCTION/METHOD/local scopes.
3. The input Stage 2.3 archive already failed the Stage 2.1 catalog regression because Stage 2.2 changed the `bool` callable contract without extending the Stage 2.1 drift guard. An explicit cumulative catalog delta now admits only the reviewed `bool` v1-v6 and `polyline.new` v5-v6 changes while preserving the prior `ta.rma` delta.

Targeted evidence after the final fixes includes 61/61 Pine2AST scope/version tests, 25/25 Stage 2.3 Ast2Python execution tests, 11/11 Stage 2.1 catalog regression tests, 42/42 Stage 2.2 regressions, 214/214 PineLib state-lifecycle tests, 85/85 Ast2Python Stage 2 language execution tests, and 77/77 overload execution tests.

Collected inventories: Pine2AST 3299, Ast2Python 1582, PineLib 5011; no shrink.

A complete execution of all 3299 Pine2AST tests after the final fixes has not completed in this environment. Under the master specification this remains an acceptance blocker, so `stage2_3_accepted=false`. GitHub was not modified.
