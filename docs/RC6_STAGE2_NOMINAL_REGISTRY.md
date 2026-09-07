# Stage 2 nominal declaration admission

This block ties enum values and UDT field schemas to the immutable declarations
of the verified generated artifact before execution or checkpoint restore.
Stage 2 remains **in_progress**; this block does not accept the entire language
matrix, recursive varip persistence, imports or builtin oracle.

## Problem and ownership

The preceding runtime admitted a source-qualified nominal ID but could learn its
member/schema metadata from a value or checkpoint. An independently reproduced
checkpoint with recomputed unkeyed hashes could change an enum member, its
ordinal, a UDT field type or its varip flag. Hash consistency alone cannot prove
agreement with the source declaration.

PineLib now owns a complete immutable `NominalTypeRegistry`. Its admission checks
exact source/version identity, closed schemas, declaration closure, ordered enum
members/titles, UDT field types and varip flags, and bounded input resources.
The heap uses that owner for construction, mutation and restore, including typed
containers and reference history. No host-side Pine type evaluator is added.

Ast2Python emits `NOMINAL_TYPE_REGISTRY` from the checked lowering plan, including
unused and forward/cyclic declarations and private dependency types retained by
the linker. The literal is covered by the existing emitted-module hash; the v3
artifact gains no wire fields. Nominal artifacts require the exact additive
`compiler.nominal_registry.v1` capability. Pre-registry nominal artifacts must be
recompiled; nonnominal artifacts retain their existing admission path.

OpenPine verifies emitted module bytes and explicitly supplies PineLib's
`NominalTypeRegistry.from_json` factory to the compiler admission helper. The
helper checks compiler artifact identity and capabilities before invoking that
factory. OpenPine passes the immutable owner to both native and protected
worker sessions before callbacks. Checkpoint candidates reuse the same owner.
The compiler helper alone cannot authenticate a namespace: its caller must
verify the bytes before executing them.

## Reproduction and tests

The initial v5/v6 reproduction deliberately recomputes the final transcript,
runtime checkpoint and outer checkpoint hashes after each mutation. The five
forgeries exercise unknown enum members, invalid ordinals, a valid member with
another member's ordinal, UDT field types and declared field persistence flags.
Positive controls preserve unchanged checkpoint round trips. Host negative tests
restore into a nonempty session and require exact prior state, transcript,
registry and output cursors after rejection. A forged varip-field list keeps the
wire's canonical sort order so an old structural check cannot mask the new check.

Runtime tests cover nominal values in scalar storage, typed arrays/maps/matrices,
erased type descriptors, committed/working array slices and reference cycles.
Compiler tests cover complete literal emission, exact capability JSON types,
source/version mismatch, immutable admission and real captured legacy artifacts.
Existing nominal tests keep their behavioral expected values. Two old runtime
fixtures now use distinct declaration IDs where the old setup reused one ID for
incompatible schemas. Two shared compiler fixtures admit the registry before
execution. These setup migrations have separate commits and receipts.

The host adds inline and locked-import cases for Pine v5/v6, native execution,
full transcript corruption and compact checkpoint continuation. Its two request
cases independently assert source closes 10/20/30 and aligned chart bars 4/9/14.
The immutable provider precomputes all source bars; cached host continuation is
explicitly distinguished from restoring the extracted real child checkpoint.

Independent review found a further hole before publication: parent restore
accepted a resealed unknown enum in a dataset's nested `compiled-runtime`, while
restoring that child directly rejected it. The runtime now validates every saved compiled child using the same session
factory and ordinary segment decoder as live execution, before replacing the
parent. An iterative queue shares depth/count/byte budgets; no evaluator or
provider fetch runs during admission. The unchanged independent reproduction
now rejects both child and parent on Python 3.11/3.13, preserving live state.
Seventy-one new tests cover forged children, source/context identities, late
child failure, recursive limits and live continuation with decoded `na`.
The initial failure and closed reproduction are both retained as evidence.

## Validation status and limits

The published baseline is `2b91a8d818d0729143a849d99952a49396bdb727`, with
6745 mandatory tests per Python and the completed builtin receipt. The Linux inventory proposal passed on both Python versions; complete joint
execution for this registry block remains pending.
Collection is not execution. Windows host collection reports the real required
`fcntl` import error; no fake module, platform skip or relaxed worker policy is
introduced. The final runtime full local suite contains 946 tests: 943 passes and three
Windows packaging failures on each interpreter, zero skips. Compiler affected
regressions pass 569 tests on each interpreter. These are separate local scopes;
the complete compiler/host execution remains a mandatory Linux gate. All 591
previous runtime nodeids remain, with 355 additions. Compiler adds 82 cases and
OpenPine adds 36, proposing 7218 total mandatory cases (6745 + 473).
Old fixture setup migrations retain existing behavioral expectations and have
separate runtime/compiler review receipts under `verification/`.

This registry proves declared nominal membership and internal schema consistency.
It does not authenticate arbitrary checkpoint content or bind every serialized
storage identity to a source variable's declaration. Valid scalar edits and
otherwise consistent rehashed snapshots are not an authenticated replay proof.
Recursive varip collection persistence, special reference types and additional
request expression syntax remain separate work. No TradingView execution oracle
or performance result is claimed.

## Reviewed Linux inventory

[Collection run 34162643652](https://github.com/s7cret/openpine/actions/runs/34162643652)
collected exact candidate `e255aff57cf6f8793f0df914d0b5b4b0faec2605` against
published baseline `2b91a8d818d0729143a849d99952a49396bdb727`. Both proposed
inventories are byte-identical, SHA256
`e3d5f108b9df39d4965634eb9eed0f7a45330780cc126f120c5a31d069cbbf6a`.
All 6745 baseline nodeids remain; the 473 additions produce 7218 mandatory cases,
with no removals or extra deselections. The provider's five existing external
live-network deselections remain unchanged.

| Component | Baseline | Reviewed candidate |
|---|---:|---:|
| Contracts | 557 | 557 |
| PineLib | 591 | 946 |
| Pine2AST | 1107 | 1107 |
| Ast2Python | 832 | 914 |
| Backtest engine | 1102 | 1102 |
| Optimizer | 281 | 281 |
| Deterministic provider | 601 | 601 |
| OpenPine | 1674 | 1710 |
| Total | 6745 | 7218 |

The independent inventory review reconstructs all 32 baseline/candidate source
archive Git trees from file bytes and modes. Exact published runtime/compiler
blobs also match their source review. The parser release `61c5032` has the same
tree as the verified builtin candidate `d2b4a0f`. Receipts are
`verification/nominal-registry-inventory-review.json` and
`verification/nominal-registry-remote-source-review.json`. This accepts collection
and the inventory change, not execution or full Stage 2.

A separate read-only state audit reproduces a preexisting checkpoint failure
immediately after an abort that retains a varip field. It occurs in both archived
builtin runtime `c90c267` and this candidate; retry/final commit restores a valid
checkpoint. That lifecycle gap and bounded `varip array<UDT>` persistence remain
next language work, outside this declaration-admission block.

## Joint-run failure and explicit dependency repair

[Run 34163246799](https://github.com/s7cret/openpine/actions/runs/34163246799)
executed all 7218 mandatory functional tests successfully on each Python 3.11
and 3.13, with no failures, errors or skips. The architecture gate then rejected
two direct Ast2Python-to-PineLib imports in the new registry admission helper.
Consequently the aggregate Stage 1 receipt, subsequent lint/package-build step
and frontend verification were not completed. This run is **not accepted**.
The twelve frozen Stage 1 observations and all 47 x 5 builtin observations were
independently checked against the immutable candidate expected files; their
success does not replace the missing aggregate gates.

The repair preserves the architecture policy. Ast2Python now requires the
host-injected `admit_registry` factory, returns its exact owner object, and
propagates owner errors. It does not import PineLib or duplicate its schema
validation. The generated namespace cannot select the factory. Both host
execution paths explicitly supply the trusted PineLib owner. Existing compiler
artifact, version, capability and literal checks remain unchanged; 25 new tests
check dependency boundaries and admission ordering. Existing fixture migrations
only supply the factory and preserve all behavioral expectations and nodeids.

Local focused validation passes 155 tests on both Python versions, and the
unchanged architecture policy reports zero issues. The corrected compiler
candidate is `d3bc3e68f5da52234013e922a62e22f97835e61b`. The proposed mandatory
inventory is now 7243 (7218 + 25), subject to fresh exact-source Linux collection,
independent inventory review and the complete joint CI. Later scalar and retained
abort changes remain excluded from this registry candidate.

Receipts: `verification/nominal-registry-failed-execution-review.json`,
`verification/nominal-registry-dependency-source-review.json`, and
`verification/nominal-registry-dependency-implementation-receipt.json`.
