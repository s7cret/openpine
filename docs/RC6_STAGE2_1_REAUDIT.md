> **Исторический документ, не текущая приёмка.** Результаты и статусы ниже относятся к прежним прогонам. Текущий статус исправленного кандидата от 18.09.2026 — `in_progress`, `full_stage2_accepted=false`. Единственный текущий реестр: `verification/stage2-current-acceptance.json`.

# RC6 Stage 2.1 — re-audit report

Date: 2026-09-15

## Verdict

Stage 2.1 was rechecked against the stricter package definition: Pine v1–v6 catalog, qualifiers, inputs, producer→target admission, exact falsey/source values, source/evidence locks, and explicit treatment of delegated/unverified rows.

**Result: corrected candidate; Stage 2.1 is not accepted.**

The reason is now narrow and explicit: local implementation/catalog integrity is green, but exhaustive independent official contract authority for all v1–v6 rows is not present. The earlier archive-anchored full reference remains useful only as a drift baseline.

## Reconciliation with Pine2AST PR #12

Remote state inspected read-only:

- repository: `s7cret/pine2ast`
- PR: `#12` (`RC6 Stage 2: stage2-block-r1c`)
- base: `release/5.0.0rc6` @ `3329e902cfb247c404a778c1260bcae02b97d378`
- head: `opwf/stage2-block-r1c-pine2ast` @ `1a42c1435a647f1859b96ea2d3cf5e1e8a09147a`
- changed files: `tests/stage2/test_catalog.py`, `tools/catalog/build_catalog.py`

Compatible work was adopted locally without merging the PR or modifying GitHub:

1. catalog builder uses a system temporary directory instead of writing `.catalog-build-tmp` inside the source root;
2. seven compact CAT-01 regressions preserve the important PR coverage for exact `barstate.*` inventory and legacy/modern `input.*` version boundaries.

The full 763-line test addition was not copied mechanically. Its acceptance-relevant invariants were retained in a smaller dedicated module, while existing Stage 2.1 input-contract tests continue to cover specialized inputs, overloads, qualifiers, `active`, and `ta.rma`.

## Catalog gate correction

The old gate conflated two different facts:

- a candidate matches a frozen prior OpenPine delivery except for reviewed deltas;
- a candidate is independently complete relative to official Pine contract authority.

Those are now separated:

- `frozen_baseline_reference.ok=true` means drift control only;
- `official_modern_name_reference.ok=true` means the official v5/v6 name payloads have no missing required names;
- `official_catalog_authority.ok=false` means exhaustive independent contract authority is not pinned for all versions;
- `catalog_integrity_ok=true` means the local catalog, target mapping, input authority, and drift lock are internally consistent;
- `stage2_1_acceptance_ok=false` prevents a false Complete/Accepted verdict.

## Inputs and qualifiers

The re-audit preserved and rechecked the following acceptance-sensitive properties:

- modern specialized inputs are not backported into v1–v4;
- legacy input rows do not survive into v5/v6;
- `input.integer` and `input.int` are distinct historical/modern identities;
- `input.source` accepts a series source and remains a series result;
- scalar input defaults and presentation/configuration parameters keep their exact qualifier ceilings;
- `active` remains an input-qualified boolean dependency in v6 and is not backported into v5;
- mutable input-derived variables cannot bypass qualifier admission;
- `False`, integer `0`, float zero, empty string, and source token selection are preserved as actual effective values rather than being lost to truthiness/defaulting;
- resolver and runtime session use the same effective-values hash for the covered falsey/source cases.

## Inventory consistency

The Pine2AST test inventory changed intentionally because the PR #12 reconciliation adds seven CAT-01 tests:

- old: 3225
- new: 3232
- deselected: 0

The new exact collection hash is stored in `verification/inventory.json` and in `stage2-1-inventory-rebaseline-review.json` with a collection receipt. The OpenPine test nodeid inventory was deliberately not changed: new resolver/session equality assertions were folded into existing test nodeids, avoiding an unverifiable host-wide rebaseline in this extracted environment.

## Current tests

Current re-audit targeted suite:

- 171 tests
- 0 failures
- 0 errors
- 0 skipped

The suite covers the new CAT-01 reconciliation plus existing Pine2AST/Ast2Python/PineLib/OpenPine 2.1 input/catalog contracts.

Pine2AST catalog generator `--check` passes.

The previous full Pinelib (4978) and Ast2Python (1554) execution receipts remain applicable because those source trees are unchanged in this re-audit. A new full Pine2AST execution was not completed in the current execution environment and is not promoted to PASS.

## Remaining authority blockers

1. Pine v1–v4: no exhaustive frozen official catalog of every symbol, overload, parameter, default, return and qualifier is included. The official migration/source manifest plus conservative historical projection provides provenance and bounded historical uncertainty, not exhaustive authority.
2. Pine v5–v6: independent TradingView reference payload indexes establish required names, and dedicated Stage 2.1 authority locks cover the input surface, but full callable contract authority is not independently frozen for every row.

Therefore the correct machine status is `stage2_1_acceptance_ok=false`. This is intentional fail-closed behavior, not a test failure hidden by the archive.

## GitHub

No branch, commit, tag, PR, review, or repository file was modified.
