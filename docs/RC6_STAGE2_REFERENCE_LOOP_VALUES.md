# Stage 2 — reference bindings, loop values and locked array-library imports

## Scope

This block connects parser, exact-target compiler, the existing PineLib reference
heap and the existing broker. It does not introduce a second interpreter, heap,
checkpoint format or trading engine. The original locked-import source commits are
preserved and now published; their earlier local-only reports are historical.

Reviewed sibling commits: Pine2AST `b5ea24f143100fd7af166e654536a83bab973b06`, PineLib
`fe653c923647cb4b2b2813afc13ebb6b3c2755b3`, Ast2Python
`0bbfd5910b3faab0070111875d37eb141794e457`. Exact full-stack pins remain in
`RC6_LIFECYCLE_SOURCES.json`.

## Reference values and state

Default/var typed array bindings now keep reference identity in normal runtime
slots and typed history. Construction allocates a distinct object for each executed
callback/occurrence. This object identity is deliberately different from a written
callsite's persistent state identity. Repeated UDF calls and a var initializer retain
their own state without either reconstructing the array every bar or accidentally
aliasing all newly constructed arrays to one heap object.

`a[1]` observes the previous reference bound to a variable, not an unconditional deep
copy of the array contents. Aliases retain shared object identity. New-instance
history and persistent-array aliasing are separate test cases. Array-instance history
is admitted in v5/v6 and rejected in v4; scalar history of `array.get(...)` remains
available under its earlier rules. Both legacy `float[]` declarations where supported
and newer `array<float>` syntax keep their exact element type.

Ordinary mutations and allocations participate in existing transaction rollback.
JSON checkpoint continuation preserves reference handles, local history, mutation
results and subsequent command tapes. Typed bindings reject mismatched handle kinds
and element types. `varip` reference declarations remain explicitly unsupported in
this compiled subset; ordinary rollback must not be advertised as varip behavior.
UDT/enum/generic reference support and complete matrix/map language integration are
not implemented merely because the heap already supports additional object kinds.

A separate real ABI defect was reproduced: the frozen `array.set` row lacked source
index/value bindings. Its explicit source signature is supplied to the manifest
builder rather than inferred from arbitrary Python argument names. The rebuilt exact
manifest includes typed reference-storage and loop-value contract revisions. The
compiler rejects a target lacking these contracts instead of emitting calls against
an older incompatible runtime. Checkpoint/ABI identities change with these policies.

## Loop results and causal control flow

Range `for`, `while` and supported array `for...in` loops return the last value from
an iteration that completed its return expression. `break` and `continue` do not
replace that result with a Python None or a partially computed value. Nested loops
have separate result state and keep lexical shadowing. Conditional tails that match
no branch produce a typed missing value, not a stale result from an earlier iteration.

Scalar, tuple and reference loop results are typed through checked IR. An empty loop
returns the appropriate missing value; tuple elements are initialized separately,
including false for a missing v6 boolean element. Loop values work as declarations
and UDF tails. Version-specific fixed/dynamic range ends and lazy/eager expression
rules continue to use the established language policy.

All emitted loop forms consume one shared callback iteration budget. Nested loops
and UDF calls do not each reset an independent allowance. Exceeding the budget raises
an error and normal transaction rollback applies. This is a bound on emitted loop
iterations, not a universal wall-clock or memory proof for arbitrary external code.
The protected-worker resource limits remain enabled.

## Array traversal and performance boundary

The live array iterator obtains length and the current element from the reference
heap. Reading one element no longer materializes the entire array first. Slice views
resolve their current parent bounds and reject invalid or cyclic views; nested
reference elements retain identity. This eliminates the reproduced repeated full
copy during traversal without changing that traversal into a frozen snapshot.

Tests forbid the old full-array accessor during a 10,000-element scan and cover live
mutation, nested slices, bounds, aliases and rollback. This is an algorithmic/copy
regression test, not an end-to-end throughput, FPS or backtest-memory benchmark.
The heap and reference history still materialize objects and are resource-bounded.

## Locked library compatibility

Scalar-only imports keep their existing `same_version_scalar_v1` linkage profile.
Checked scalar-array exports and array traversal use an explicit
`same_version_arrays_v2` profile. The linkage receipt, linked-source verification
and artifact dependency identity bind the selected profile and exact reachable
source graph. Old scalar fixtures retain their existing identity.

Pine 5 libraries with a Pine 5 consumer and Pine 6 libraries with a Pine 6 consumer
can pass and return supported scalar arrays, use private helpers and preserve a var
array across calls. This does not admit arbitrary nested arrays, UDTs, exported
methods/enums, cross-version linking, object captures or request-dependent library
bodies. Sources remain explicitly captured offline; automatic provider/UI discovery
is not added. No library filesystem or network access is granted to the worker.

## Executable integration checks

`rc6_tests/test_rc6_reference_loop_values.py` contains 29 cases: 25 run locally and
four require the real protected-worker environment. The main matrix combines Pine
5/6, direct/imported UDF, on-close off/on and fill-recalculation off/on. It retains a
var array, reads prior array-instance history, traverses with continue and computes a
while-loop tuple with break. On bar 2 it must issue exactly one qty=3 order and fill
at 102 on-close or at the next open 103. Both transports compare actual broker tapes,
trades and equity. Checkpoint tests separately compare generated-session continuation;
they do not claim complete broker/IPC restart.

The new parser/runtime/compiler files add 35/33/95 cases. Together with the 29 host
cases the frozen joint inventory grows by 192 from 5,137 to 5,329. These counts include
the existing review-accounting and architecture infrastructure; they are not a Pine
compatibility percentage. Full observed CI results are recorded only in the later
publication receipt, not inferred from collection or source presence. All six library
suites, deterministic provider selection, native/affected host paths, original Stage 1
corpus and frontend gates remain required. Five provider live-network cases are the
existing explicit exception.

## Stage 2 acceptance remains open

This block removes the reference-binding, array-history and loop-result barriers in
the described subset. It does not close the entire original Stage 2. Remaining
criteria include a complete version-exact language catalog and independent numerical
expectations for every admitted builtin, generic/reference/UDT/enum and corresponding
library exports, varip references, full once behavior around historical fill
recalculation, and remaining import/method/version combinations. Those criteria are
not deleted, renamed into later stages or marked accepted because unsupported calls
produce a clear error. `verification/stage2-progress.json` remains `in_progress`.

Official semantic references, not external execution-oracle evidence:
- https://www.tradingview.com/pine-script-docs/language/loops/
- https://www.tradingview.com/pine-script-docs/language/arrays/
- https://www.tradingview.com/pine-script-docs/v4/essential/arrays/
- https://www.tradingview.com/pine-script-docs/concepts/libraries/
