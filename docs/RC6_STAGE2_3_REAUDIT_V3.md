> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# OpenPine RC6 — Stage 2.3 third re-audit

Status: **corrected candidate; not accepted**.

The third re-audit did not find a new UDF/runtime semantic defect after the v2 fixes. It did expose two stale historical regression guards: the valuewhen whole-pack guard and recursive-length whole-pack guard still compared Stage 2.1 frozen expectations directly against packs that legitimately contain reviewed Stage 2.2/2.3 catalog deltas.

The repair does **not** rebase expected values. A sealed local copy of `stage2-3-cumulative-catalog-delta.json` is used by `restore_stage21_baseline()` to fail-closed rollback exactly the reviewed `bool` v1-v6 and `polyline.new` v5-v6 rows, then the pre-existing `ta.rma` v5-v6 delta, before the original Stage 2.1 hashes are checked. Any unrelated catalog drift still fails.

Evidence after the fix:

- historical whole-pack guards: 103/103 PASS;
- Pine2AST Stage 2.3 scope/global-binding/guard targeted set: 176/176 PASS;
- Ast2Python Stage 2.3 execution: 25/25 PASS;
- Stage 2.1 catalog regression: 11/11 PASS;
- Stage 2.2 PineLib regression: 39/39 PASS;
- catalog generator `--check`: PASS;
- PineLib ABI `--check`: PASS.

A complete execution of all 3299 Pine2AST tests **after this final v3 guard fix** has not completed in the available environment. Coordinated protected-worker and Python 3.11/3.13 package gates were also not rerun. Therefore `stage2_3_accepted=false` remains mandatory.
