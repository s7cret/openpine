# Stage 2 — mixed ordinary-function and method declaration families

Local cumulative continuation from `88b7df08fbef36ea66dac2dfd16f2b555ef53f55`.
No remote refs, PRs, Actions or published releases are modified. Full Stage 2
remains in progress; this is a bounded implementation with engineering evidence.

## Root failure and one selection owner

Previously an ordinary `f()` excluded every same-named method from function-style
calls, even when the argument types distinguished them. Reversing the declaration
order could additionally produce a false redeclaration. Merely allowing the name
would not repair binding, local types or compiler contracts.

The existing FunctionCandidates inventory now forms a combined family for explicit
calls (`f(receiver, ...)` or `lib.f(receiver, ...)`). MethodCandidates supplies the
explicit-receiver signature shape, and the existing SignatureResolver selects one
exact declaration across both kinds. Method/function consumers use the same cached
selection. Invalid or ambiguous calls cannot fall back to the last bare-name symbol.
Receiver-dot notation continues to select **methods only**. Ordinary functions are
not automatically extension methods. Builtin namespaces keep their existing owner.

Candidates are filtered by lexical availability and exact source visibility before
type/qualifier/default/named binding. Mixed singleton functions receive distinct
semantic keys too, so their return facts do not overwrite same-named method facts.
The type rules and scoring are not duplicated. Candidate work and caches remain
bounded; there is no dynamic runtime dispatcher or second interpreter.

Example: `f(float x)=>x+0.5` and `method f(int self)=>self+1` select the method
for `f(2)` (3) and the function for `f(2.0)` (2.5). Tests also cover required arity,
string/bool, arrays/maps/matrices, UDT, tuples, named/default arguments and multiple
members of each kind. Both declaration orders are exercised.

## Lexical inference repairs found by integrated execution

A return expression was re-inferred **after** leaving its local block, against the
flattened symbol table. Two bodies both declaring `n` could therefore give an int
method the float return type of another function. The scope walker now uses the
return-expression fact recorded in that scope; the existing fixed-point inference
pass refines incomplete facts later.

Field reassignment targets now capture their receiver and field facts while in
scope. Otherwise a method's `self.field` could be rebound to an ordinary function's
`self` parameter during later fact collection. No runtime mutation is performed by
this analysis and no type is guessed from the output.

Callable inference reuses selected parameter bindings instead of inferring a
method argument index without its explicit receiver. Temporary `function`/`method`
predeclaration markers are excluded from concrete value-type merges. This lets a
transitive helper refine its result after its selected method body is analyzed.
These are repairs to shared inference ownership, not per-test compiler workarounds.

## Library and consumer contract

`same_version_mixed_callables_v7` is profile revision 7, covering Pine **5 and 6**.
During library preview, both ordinary and method declarations are retained for a
mixed namespace call. Private helpers and direct-import visibility remain enforced
before resolution. Public overloads cannot be captured by a more specific private
function/method; a transitive dependency is not implicitly a direct import.
Selected declarations and call tokens are projected without wrappers or changes
to body/argument evaluation. Different source-unit namespaces never merge by name.

The `mixed_user_callable_families_v1` capability is derived from bounded AST
structure and the reconstructed sealed library context. It is checked exactly and
replayed at source-free admission. Resealing a substituted declaration, actual type
or constant does not make it admissible. Mixed bundles trigger full bounded input
preflight. This is a compiler capability, not an invented PineLib runtime operation.
Older consumers must reject it; recompile after updating the coordinated packages.

## Independently specified expectations

Manual expectations are derived from Pine fixture bodies, not OpenPine observations:

- Separate written counters: 1/2/3 and 10/20/30. Another written call in a three-step
  loop: 3/6/9. Checkpoints after different historical cuts match continuous execution.
- Ordinary and method calls on UDTs retain reference aliasing and field rollback.
  Ordinary/varip sums on ticks are 22/22, 22/33, 22/44; midbar restore agrees.
- SMA inside a selected method uses its own length input and warmup; same-named
  string function results do not affect its qualifier or state.
- A private more-specific int declaration stays internal: the public float call
  returns 3 for 2, while the library's own helper can return 102 from its private body.
- ArtifactStore persistence/readback and the existing broker produce qty=33 on
  bar 2 from mixed counters 3 and 30, at price 102 on close or 103 next open.
  Pine 5/6, local/imported, both transports and fill recalculation are exercised.
  In-process compare_modes is not sandbox evidence; four separate tests require
  actual protected workers and are mandatory in future joint CI.

## Scope limits and preserved acceptance

This profile implements distinguishable mixed signatures and deterministic rejection
of equally applicable explicit candidates. No external TradingView execution was
obtained for exact function/method collisions or every optional/qualifier-only
ambiguity, so this is **not** a claim of complete cross-kind collision parity.
Full catalog, cross-version imports, wider generic contexts, historical builtin
expectations and all original Stage 2 exit criteria remain authoritative.

STATE-02 and IMPORT-03 remain partial while gaining this tested local subprofile.
All original 16 work packages and previous mandatory test IDs remain. Existing
numeric expectations/assignment locks are unchanged; the prior unverified v5
str.tonumber disagreements are not silently fixed or credited to new source pins.
No new whole-product performance, Python 3.11, GitHub CI or browser acceptance is
claimed. Final current executions and source heads are in the local receipt.

## Primary sources (not execution oracles)

- https://www.tradingview.com/blog/en/method-syntax-in-pine-script-36909/
  User-defined methods can also be used as ordinary functions.
- https://www.tradingview.com/pine-script-docs/language/user-defined-functions/
  Required signature distinctions, lexical function scope, per-written-call state.
- https://www.tradingview.com/pine-script-docs/language/methods/
  Method receivers and overload declarations.
