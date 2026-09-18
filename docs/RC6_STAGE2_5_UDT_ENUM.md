> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# OpenPine RC6 — Stage 2.5 UDT and Enum (strengthened)

Date: 2026-09-17

## Verdict

**Stage 2.5 ACCEPTED as a strengthened package gate.**

`full_stage2_accepted` remains false.

## Owner

Single semantic owner: pine2ast nominal binding, ast2python lowering, pinelib heap/registry. No parallel UDT/enum implementation.

## DoD coverage

Every item from TZ 7.6/7.7 that belongs to package 2.5 is represented by a positive or negative test in the official Stage 2.5 modules or the locked nominal suites they sit on:

declaration, constructor, defaults, field read/write, copy, nested references, arrays/maps of UDT, function args/returns, history, rollback, checkpoint, enum members/titles, comparisons, switch, UDT fields, library export/import, serialization, same-name library isolation, private export, unknown member, wrong version, foreign type.

UDT methods/`self` are included as part of the UDT surface. Broader method overload families across collections remain Stage 2.6.

## Status

`stage2_5_accepted = true`
`stage2_5_strengthened = true`
`full_stage2_accepted = false`
