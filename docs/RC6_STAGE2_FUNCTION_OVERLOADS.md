# Stage 2 — declaration-bound ordinary function overloads

Local cumulative development after consumer-library delivery `e85021a7233adb083fd9631856bf24203d513b7c`.
No remote refs, PRs or Actions are changed by this block. Full Stage 2 remains in progress.

## Root gap and shared mechanism

Previously `f(int x)` and `f(float x)` could not coexist: the frontend rejected
`f` as a duplicate, while parameter, return-type, qualifier and call-context tables
also retained only one declaration per name. Permitting duplicate spelling alone
would silently select the wrong body or state. The new FunctionCandidates owner
indexes immutable declaration identities and delegates all actual type/argument/
qualifier matching to the existing SignatureResolver. Declaration prepass,
parameter/result inference, contextual facts, validation and binding consume that
same selected identity. Ordinary singleton paths retain their previous identities.

Supported profile: Pine 5/6 local functions and exact locked same-version library
exports. Distinct required arities can use inferred parameters; equal-arity
families must have distinguishing required qualified type signatures. Parameter
names, optional-only changes and return types alone do not distinguish overloads.
Invalid/ambiguous calls and recursive declaration cycles fail before emission.
The new profile does not silently backport overloads into the older v1–v4 profile.

## Library projection and admission

Existing source-scoped callable projection now retains every overload during the
typed preview. Visibility restricts candidates by the defining source unit,
explicit namespace and public/private status *before* selection. Only then is the
selected declaration and its callee token alpha-renamed. A private int overload
cannot win a public call merely because it is more specific than a public float
one. Transitive imports do not become direct imports; two libraries' identically
spelled functions remain separate. Source bodies, written arguments, order and
callsite locations are not wrapped or reimplemented.

The profile `same_version_function_overloads_v6` has **revision 6**; it covers
Pine versions 5 and 6, not just Pine 6. Compiler capabilities
`user_function_overloads_v1` and `library_function_overloads_v1` explicitly bind
local AST families and reconstructed library projections. Source-free admission
replays bounded AST semantics using the existing producer decoder/analyzer. Tests
reseal all semantic-fact references and still reject a substituted declaration,
qualifier, default or constant. The library context is rebuilt from locked source;
a checksum is consistency evidence, not proof of author trust.

The compiler admits the new contracts and lowers the selected body through the
existing invoke_function_v1/runtime/heap/broker path. These are compiler features,
not fabricated PineLib capabilities. No dynamic runtime overload dispatcher,
second evaluator, function-name alias fallback or source-code wrapper was added.

Duplicate signature indexing is linear in signature input size, rather than a
pairwise family scan. Candidate inference is cache-bounded and work-bounded;
exhaustion yields a diagnostic, not a wider fallback. This is an algorithmic
property, not a measured whole-product speedup.

## Executed expectations

Manual fixtures exercise int/float/bool/string/color, arrays/maps/matrices,
nominal UDT/enum arguments, named/default arguments, return aliases and tuples,
completed-loop results, typed local shadowing, earlier-overload calls,
private/transitive exports and independent SMA inputs. No expectation is copied
from OpenPine output or labelled a recorded TradingView result.

- `f(int x)=>x+1` and `f(float x)=>x+0.5` return 3 and 2.5 for 2 and 2.0.
- Stateful int/float overloads at separate written callsites return 1/10,
  2/20, 3/30; another written call in a three-iteration loop returns 3/6/9.
- Conditional execution preserves local history across actual calls: counts
  1/2/3 on alternate bars refer to missing/1/2, not skipped global bars.
- Returning a UDT or array preserves intended aliasing: a mutation to 9 through
  the returned alias reaches the original object, but a new object remains 3.
- On realtime ticks, ordinary fields roll back while explicit varip fields
  retain updates. Sum pairs are 22/22, 22/33, 22/44; midbar JSON restore agrees.
- A persisted/reloaded library artifact produces real broker entry qty=33 on
  the third bar, at 102 on close or 103 at next open. Pine 5/6, local/imported,
  both transports and fill-recalculation are exercised. compare_modes substitutes
  transport in-process; four separate mandatory tests require real Bubblewrap.

## Boundaries and acceptance

STATE-02 and IMPORT-03 gain this implemented local overload profile, but remain
partial. Mixed same-name ordinary function/method families, cross-language-version
libraries, all remaining generic/reference contexts, full versioned catalogue and
complete independent builtin expectations are not accepted by these tests.
No new claim is made about every side-effectful default or argument-order case.

Existing numeric corpus/expected/assignment locks are unchanged. The prior strict
index had unresolved v5 str.tonumber expectations; its result is neither changed
nor reused as evidence for the new producer/compiler pins. Current full numeric
index, Python 3.11, protected Linux CI and frontend need their own acceptance.
The original four criteria, 16 packages and all previously mandatory test IDs stay.

Detailed final executions, exact heads, initial failures and cumulative delivery
identities are recorded in the separate local receipt. No old CI is attributed to
this source. Update the coordinated package set and recompile artifacts; do not
load a new overload-bearing bundle with an older consumer that lacks its feature.

## Primary semantic sources (not execution oracles)

- https://www.tradingview.com/pine-script-docs/v5/release-notes/#function-overloads
  November 2021: function overloads were added during Pine v5, with distinct
  arities or explicitly typed differing combinations for equal arities.
- https://www.tradingview.com/pine-script-docs/language/user-defined-functions/#function-overloads
  Required parameter signatures, optional-only/return-only ambiguity, and calls
  to earlier defined same-name overloads.
- https://www.tradingview.com/pine-script-docs/concepts/libraries/
  Typed export boundaries and library qualifier constraints.
