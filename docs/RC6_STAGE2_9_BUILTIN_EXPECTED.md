> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# OpenPine RC6 — Stage 2.9 Independent Builtin Expected Values

Date: 2026-09-17

## Verdict

**Package gate for the locked executable subset only.**

`full_catalog_accepted = false`
`independent_builtin_expected = partial`
`full_stage2_accepted = false`

`str.contains` is listed TARGET_DIRECT in the runtime manifest, but the
`substring` parameter is `UNBOUND_FAIL_CLOSED` and the producer catalog only
exposes `source`. It is **not** an accepted executable builtin.

Unbound/incomplete rows stay in the denominator.
