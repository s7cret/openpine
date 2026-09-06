# Stage 2 — linked scalar language execution core

Stage 2 is IN PROGRESS, not accepted as the complete language layer. This block
implements related frontend, lowering and runtime semantics under the Stage 1
architecture and inventory gates. The original OP-02/13/14/16/17/18/19/31 scope and
eight-stage plan remain authoritative; passing this block does not close them all.

## Coordinated source set

Pine2AST `e84931f80fb9e9077ede3d71bce37c1fb22b601a`;
PineLib `3e08fe73bd9df412c742e0d27a78013f9d4e5f2a`;
Ast2Python `1fadbb140a49e34dc433d45c5ee8ebae39d94c17`.
Use all pins in RC6_LIFECYCLE_SOURCES.json together and recompile artifacts. The
runtime policy and ABI identities changed. Old checkpoints must not be assumed
compatible merely because package versions match. Old global each-bar series
serialization remains unchanged; local evaluated-history adds explicit fields.

## Implemented linked behavior

- `once` is a v6 conditional structure, not a variable mode or a compiler-side
  Python boolean. Its predicate is not evaluated after committed completion;
  ordinary PineLib transaction slots own its rollback and checkpoint state. The
  frozen migration input stays intact; the catalog build applies a versioned overlay.
  Older catalogs still permit the ordinary identifier `once`.
- Scalar `if` and `switch` values, final declarations/reassignments and conditional
  function returns are emitted from checked IR. A missing bool branch/history
  produces false in v6 and missing values in older versions. NA uses the canonical
  runtime representation; invalid v6 bool forms still fail before execution.
- Lexical declarations and written UDF calls own their state. Shadowed names do
  not alias globals or unrelated branches. Nested written calls have independent
  paths. Loop iterations reuse a written callsite rather than creating a new one.
  Positional/named/default scalar parameters bind to the generated function names.
- Typed local/parameter histories advance only on bars where the scope is evaluated.
  Multiple calls of one written site in a loop produce one committed final value
  per evaluated bar. Expression histories and TA state inside different written
  UDF calls use the same existing runtime storage, with distinct identities.
- Generated evaluation follows the catalog: ternary is eager through v3 and lazy
  from v4; logical and/or is eager through v5 and lazy in v6. The existing ternary
  catalog was correct; the generator's unconditionally Python-lazy behavior was not.
  Positive-step inclusive ranges handle descending direction and v6 dynamic end
  bounds. A per-loop iteration limit is explicit runtime policy, not a TV quota.

No second broker, interpreter, module-global state cache or alternate checkpoint
format was added. Checked compiler operations point to PineLib primitives. The
existing exact-target required-primitive checks from Stage 1 are retained.

## Scope and limits that must remain visible

This is the scalar/control subset. Full version-exact signatures and overload
coverage, Pine library imports, generic/reference/UDT and persistent collection
capture, loop-result/control-transfer edge cases, all builtins with independent
numerical expected data and the rest of the 2026 feature package remain open.
UDF dependency slicing inside request expressions is not added by ordinary UDF
execution support. Full external TradingView equivalence is not claimed.

The host uses ordinary rollback for once. Direct runtime/compiler tests cover
nonclosing realtime ticks, final-tick conditions, varip side effects, abort and
JSON continuation. Historical fill recalculation has additional callback ordering;
its full once parity remains open. The four protected-worker scenarios deliberately
include `strategy.position_size == 0` inside once, so they prove that guarded order
scenario, not a universal exactly-once guarantee for irreversible orders during
closing-fill recalculation. Checkpoint continuation tests at committed bar cuts
also test an unguarded once body without fill callbacks. No varip completion hack
was introduced to hide this boundary. Full isolated-job restart remains Stage 4.

Native loop resource policy currently bounds a range invocation. Whole-callback
budget aggregation and arbitrary while/recursive-resource combinations are not
claimed complete. The existing sandbox remains mandatory.

## Verification and fixture accounting

The three library suites are 438 / 297 / 462 cases, with 25 / 68 / 85 new cases.
Nineteen host cases add sparse history, scalar branch defaults, dynamic range,
independent call state, exact order quantities and three checkpoint cuts. Four are
real protected-worker cases across interactive/bulk and on-close off/on. The
combined proposed inventory is 4,986 cases, including 37 existing review-accounting
checks. Actual joint results must be read from the publication receipt, not inferred
from collection or this document. The fixed twelve-case Stage 1 corpus is unchanged.

Twenty-eight consumer bundles and twenty-two normative identity fixtures in the
compiler are regenerated through existing tools. Original Pine sources and manual
trade expectations are not replaced by output from the changed implementation.
The concrete AST inventory grows from 38 to 39 and compiler operations from 53 to
54 for once. Former local-var/shadow rejection tests become positive behavior tests,
not skipped cases. The exact changed test IDs are recorded in inventory.json.

The library publication gate verifies readable diffs, regenerated full trees, raw
source commits, full test counts, lint and wheel/sdist builds on Python 3.11/3.13.
The first PineLib run passed all 297 tests but stopped on import-placement and test
lambda lint. The corrected run preserves all behavior/assertions and passes lint.
This is not an unreported functional failure or a reason to disable the gate.

## Official semantic references (not execution-oracle evidence)

- https://www.tradingview.com/pine-script-docs/language/conditional-structures/
- https://www.tradingview.com/pine-script-docs/language/user-defined-functions/
- https://www.tradingview.com/pine-script-docs/language/execution-model/
- https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/
- https://www.tradingview.com/pine-script-docs/v4/release-notes/

Next acceptance work remains within Stage 2: complete the agreed language/catalog,
imports and independent builtin corpus instead of declaring the entire stage done
on the strength of this connected subset.
