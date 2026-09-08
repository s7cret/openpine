# Exported/imported library methods: B/C design after A2

Date: 2026-09-08. Status: read-only architecture and independently authored before
evidence, for root review. No production/schema/test/corpus edits, publication,
new runtime feature or full Stage2 acceptance is included.

## Frozen foundation and actual remaining failures

This supersedes the *planning* sections B/C of `exported-library-methods-design.md`
(`ff33f79f2296ac971aa4e905b3c9bc96141c9342c793f061c3c06dc98c4d2f1a`).
That file and its original 62 observations remain untouched. Its ordinary method
inventory and explicit receiver gaps have since been handled by A1 and A2;
neither should be reimplemented in the linker.

The new read-only copy is `exported-methods-bc-design-source`, verified against
A2 source bundle `b3ad0eeacb82f16cb1791522e0ecc5699be7331a0620ac58f875acd73a09aa74`
and owner receipt `a72377a866bfc6a7688ee05d8906a5cc749708d9cb349aa3e928323ddc2634ff`.
It contains exactly 417 producer, 176 compiler and 171 runtime source files.
The underlying revisions are producer 05d7 + A2, compiler 95a + A2, runtime 2148.
All 764 file hashes were checked before and after probes. A2 independent review
47789609 has since closed; its Linux joint execution is a separate pending gate.

`exported-methods-bc-manual-v1.json` was saved **before** importing the SUT:
SHA `d0988c5ddba3464fb0df4e3b7692540d03bc1012f2685037060dd0c016ac23d8`.
Its 72 rows comprise 34 scenarios for each of v5/v6 plus four v1-v4 syntax
controls. Values/traces are literal arithmetic, not recordings. Six modern rows
are explicitly UNVERIFIED questions (namespace form, transitive root visibility,
cross-library tie priority), not positive or negative Pine oracles.

The corrected before outputs on both Python versions are byte-identical:
`exported-methods-bc-before-corrected-py311/313.json`, SHA
`9e0d305ec70c7b2626362f77b88ce64e237874dbde95ce9e2b5761b2924f110b`.
There are 60 link-stage stops, six bundle-stage stops, two successful legacy
function controls, and four historical syntax rejections. The legacy controls
execute `[5,5,5]` and preserve JSON checkpoint continuation. The initial harness
read capabilities from the wrong top-level key in the two legacy controls;
its script/output are retained separately. The corrected reader uses the existing
`consumer_contract.required_capabilities` path; no expected values changed.

| Concrete current trigger | Actual owner boundary |
| --- | --- |
| `export method plus(simple int self, simple int amount)` | Standalone declaration parses; linker raises `P2A_LIBRARY_EXPORT_SHAPE`. |
| Two `plus` declarations with distinct receivers or ordinary argument types | `_parse` raises `P2A_LIBRARY_NAME` before the shared method selector sees them. |
| Exported function calls an own private `x.hidden()` | Linking succeeds, omits the private method, and bundle construction rejects projected semantics. |
| Root attempts an unexported `x.hidden()` | Rejected, but this alone does not prove privacy once private methods are actually projected. |
| Exact requested publication is absent but a newer revision exists | Existing store raises `P2A_LIBRARY_MISSING`; preserve this independently effective control. |
| Locked dependency cycle | Existing `P2A_LIBRARY_CYCLE`; keep import-cycle handling distinct from recursive UDT fields. |

Most negative rows stop at the earlier export-shape gate. They do **not** currently
prove receiver qualifiers, private public-interface types, exported result floors,
argument validation or checkpoint behavior of imported methods.

## Primary authority, separated by version

The archived official responses, decoded HTML hashes, short verified excerpts and
retrieval dates are in `exported-methods-bc-primary-authority-checked.json`
(`8ebcb4b9b01d069c338a43f09eea0179496b17e8d12ba010a78494e3ef8da165`).
Raw transport responses were gzip encoded; both raw and decoded bytes are retained.

* [v5 libraries](https://www.tradingview.com/pine-script-docs/v5/concepts/libraries/)
  identifies exports as the imported interface, requires explicit publication
  revisions, and describes typed parameters, result floors and public nominal
  types. These are affirmative v5 rules, not inferred from current v6.
* [v6 libraries](https://www.tradingview.com/pine-script-docs/concepts/libraries/)
  includes functions and methods in exported-callable restrictions. Preserve the
  existing simple/series floor and const-global capture rules through projection.
* [v5 methods](https://www.tradingview.com/pine-script-docs/v5/language/methods/)
  and [v6 methods](https://www.tradingview.com/pine-script-docs/language/methods/)
  document typed receiver dot calls, export syntax, different parameter signatures
  and user/builtin overload coexistence. A1/A2 already own these rules.
* The [February 2023 v5 release notes](https://www.tradingview.com/pine-script-docs/v5/release-notes/#february-2023)
  introduce methods. v1-v4 are not silently given modern method syntax.

Inherited A2 authority remains exact: explicit simple/series receiver syntax in
v5/v6; v6 reference annotations retain source provenance but effective series;
v5 simple-reference exception remains an UNVERIFIED admission profile. B/C does
not turn that profile into a historical Pine rejection oracle, add const/input
receiver syntax, or infer optional receiver defaults.

Current consulted primary sources do not settle user-method function form
`lib.method(receiver, ...)`, indistinguishable cross-library tie priority, or
whether importing Outer implicitly exposes Inner's methods to the root. No new
namespace/function call form or guessed tie priority is proposed. Rejecting an
indistinguishable cross-library tie is a fail-closed implementation profile, not
an independently established Pine rejection rule. Direct imports
inside Outer must work in Outer's own source scope. The root's implicit access to
transitive methods stays a separate authority question. Mixed-language library
execution is likewise outside the existing same-version profile; its current
rejection is an implementation boundary, not proof of a Pine language prohibition.

## B: one declaration/visibility owner feeding the existing selector

The current owner is `semantic/method_candidates.py::MethodCandidates`, using
`NodeIndex` declaration IDs, full receiver types and `SignatureResolver`.
The older design's proposed second candidate extraction is now unnecessary.

### Declaration identity and visibility

Add an internal immutable `CallableOrigin` keyed by:

```
(exact publication ref, raw source hash, original NodeIndex declaration_id)
```

Keep normalized source hash/span as separate evidence because AST spans refer to
normalized text. The row also records declaration kind, original lookup name,
export status and projected declaration ID/span. Receiver TypeRef, explicit
qualifier, parameters and defaults are derived from the actual canonical node;
they are not caller-provided signature authorities. Identical text published
under different refs remains distinct. Two aliases of one exact ref share one
declaration inventory while their written calls retain separate callsite IDs.

Replace `_Unit`'s method-free inventory with declaration-ID storage and indexes
for functions/constants/types and method lookup names. Existing nonmethod
renaming and serialization stay unchanged. `selected`, `visiting`, `order` and
`require` need origin keys for methods, not `(ref, method_name)`. Existing alias
validation and exact locked dependency graph remain the only import resolver.

Derive `MethodOrigins` from that graph and exact projection spans. A call originating
inside a library sees that module's own methods plus the exported methods of its
direct imports. A root call sees local methods and directly imported exports.
Private helpers copied from another module do not become root-visible. The
provisional direct-import visibility rule is a bounded supported profile; do not
label unresolved transitive-root behavior fully implemented Pine parity.

Proposed narrow internal APIs (names are for review, not implemented):

```python
MethodOrigins.from_projection(program, *, locked_units, projection, import_edges)
MethodOrigins.origin_for(node_id) -> SourceOrigin
MethodOrigins.lookup_name(declaration_id) -> str
MethodOrigins.visible_candidates(call_node_id, receiver_type, lookup_name)
    -> tuple[declaration_id, ...]
MethodOrigins.exported_declaration_ids(program) -> frozenset[int]

MethodCandidates(analyzer, program, *, origins: MethodOrigins | None = None)
MethodCandidates.resolve(call, engine) -> MethodSelection | None
```

The origin factory is called only by the linker from parsed locked bytes. It is
not a serialized callback, mutable whitelist or public `ParseOptions` override.
Admission reconstructs it, never accepts supplied visibility results. The
existing producer semantic entrypoint must factor its analyzer setup once to
accept this trusted internal derivation; do not create a second analyzer setup,
type resolver, qualifier calculator or overload scorer inside the linker.

`MethodCandidates` indexes the original lookup name when origins are present,
then applies the origin filter before its existing resolver invocation. Full
receiver type equality, `ReceiverArgumentEvidence`, ordinary named/default
binding, builtin coexistence and failed-candidate budget charges remain owned
by the current selector. Cache identity must include the immutable origin context
and source call identity. Duplicate declarations are compared inside their real
module/signature domain; generated unique names must not hide original duplicates.
Filtered-out user methods cannot reappear through the legacy synthetic-call
fallback. This requires an explicit negative control at binder/inference, not
merely checking a final winner's name.

### Projection and closure without new execution

Use a bounded provisional source with nominal names and ordinary function/type
references projected by their existing owners. Method declarations may have unique
generated names, but lookup/member tokens stay associated with original names
through `MethodOrigins`. Receiver type inference uses this normal provisional AST;
there is no linker-side receiver inference.

Run the shared frontend to obtain selected method declaration identities and
ordinary function dependencies. These facts authorize edits of **only** each
selected method member token and its declaration name. Ordinary arguments and
receiver expression nodes are never inserted, reordered or duplicated. Rewrite
both private helper calls inside their proper origin and public receiver calls.
The final normal frontend then sees deterministic unique names and must select
the same original declaration and argument/default/receiver facts after projection.

An exported ordinary function calling its own private method activates method
closure even if the root never directly calls a method and the library exports
no method. Selection must follow that function body; an export-name-only trigger
would retain the demonstrated missing-private-helper defect.

Keep all public methods for public-interface checking, plus the needed private
callable/type/constant closure. If pruning can change aggregate inference, retain
the provisional declaration closure in the new method profile instead of adding
an unbounded inference/relink fixed point. Declarations do not add call execution;
library demonstration globals, input calls and nonconst captured state remain
excluded by the existing owners. No wrapper, extra persistent variable, runtime
dispatcher or receiver proxy is allowed.

There should be at most a provisional and final semantic pass for one link.
Each pass preserves the existing `MAX_METHOD_WORK=262144` and `MAX_METHOD_CACHE=4096`,
so even failed candidate bindings remain bounded; graph/AST traversal needs its
existing independent count/depth/byte limits. This yields an explicit upper bound
of two owner method budgets per link, not an unlimited fixed-point loop. If a
third pass or a changed legacy budget becomes necessary, return a concrete repro
and architecture delta for review first. Context reconstruction must not recurse
back through its own public admission factory while building the provisional AST.

The old method-free projection and context 1.1 remain byte-preserving. An unused
private declaration alone should not force an observable method profile when no
method is selected or public-interface-relevant. A formerly accepted self-sealed
receipt whose semantics actually change under correct method resolution may need
an explicit recompile boundary; do not hide a legacy parser fork or skip full
receipt reconstruction to retain it. Capture any such real old fixture first.

### Public types, qualifiers and state

Reuse `resolve_type`, `public_type_allowed`, `visit_type` and the existing nominal
projection for the receiver and every ordinary parameter/default/return. Extend
public-interface checking to all exported methods, including unused exports and
nested collection/UDT/enum references. The original type graph, not regex matching
of a method's display name, determines public/private ownership. Recursive UDT
field graphs remain valid; method/function call cycles use the existing controlled
recursion policy and bounded graph traversal.

The current callable qualifier owner already computes
`max(actual_return_qualifier, simple)` for exported declarations. Generalize the
derived projected-export ID set from functions to callables; do not add a second
floor implementation. There is one concrete necessary integration hunk:
`MethodCandidates.entry` and `PineInferenceEngine.infer_qualifier` currently consume
the precise method return qualifier only with an explicit receiver annotation.
Projected/exported method identity must also activate that existing path, including
methods whose receiver annotation is absent. Ordinary A2 absent-field behavior
must remain unchanged outside this admitted new provenance. No exported method
receives const-value evidence because its body happens to return a literal.

Reference identity, ordinary/varip field rollback, method-local `var`/`varip`, loop
call contexts and defaults stay in existing compiler/runtime owners. Nominal IDs
continue using the exact projected source/declaration identity. Do not persist a
new library-method ID in heap state or change registry payloads.

## Minimal explicit protocol boundary for review

Proposed new linkage discriminator: `same_version_methods_v5` under the existing
closed `pine2ast.linked_libraries.v1` receipt schema. Its declaration rows require
origin kind/ID/span and projected kind/ID/span plus source provenance; old profiles
retain their exact old row shapes. An old reader already rejects the unknown profile.

Proposed context schema: `pine2ast.library_callable_context.v2`, carried in the
existing `library_context` field of consumer bundle `pine2ast.consumer_bundle.v1`
with **schema_version 1.2.0**. It contains the complete verified linkage receipt,
all-and-only exported callable rows, method-origin/visibility derivation and
canonical content hash. The existing AST `library_qualifier_context_ref` can bind
this exact context hash; it is consistency provenance, not authorship authentication.
No additional receiver fact, AST revision 2.2 or artifact-v3 field is needed.

Proposed additive consumer-only capability: `library_callable_context_v2`.
For 1.2, retain `library_qualifier_context_v1` as a prerequisite for the existing
floor contract, and require the new capability for method origin/visibility proof.
This is a capability prerequisite, not a claim that the payload has v1 schema.
Minimum consumer package version remains the actual `5.0.0rc6`; exact schema and
capability negotiation distinguish support. Root should review this explicit
prerequisite choice before implementation; it preserves the old floor capability
in generated metadata and avoids an invented runtime manifest capability.

Let B be the current base capabilities, Q the existing library floor capability,
M the new method-library capability, and R the A2 receiver capability:

| Feature | Pine | AST | Bundle | Context | Exact extra consumer caps |
| --- | --- | --- | --- | --- | --- |
| Ordinary legacy | existing supported versions | 2.0 | 1.0.0 | absent | none |
| Ordinary explicit receiver | 5/6 | 2.1 | 1.0.0 | absent | R |
| Old function/nominal library context | 5/6 | 2.0 | 1.1.0 | v1 | Q |
| Same, with ordinary explicit receiver | 5/6 | 2.1 | 1.1.0 | v1 | Q,R |
| New method library, receiver annotation absent | 5/6 | 2.0 | 1.2.0 | v2 | Q,M |
| New method library, any explicit receiver annotation | 5/6 | 2.1 | 1.2.0 | v2 | Q,M,R |

All unknown revisions, missing/extra/duplicate/substituted caps, mismatched context
schema/marker/profile, method-profile downgrades and v1-v4 requests reject. A2's
whole-bundle replay dispatch stays restricted to its exact 2.1/R contract; ordinary
2.0 does not acquire another accidental replay-only budget. New v2 context parsing
and reconstruction reuse the existing bounded context/store owners. Keep their
64 MB context, 500000-node/depth16 and store 64-library/1 MB-source/8 MB-total/depth24
ceilings unchanged unless an independently justified limit proposal is reviewed.

Extend the existing context admission owner with an exact schema dispatcher and
v2 reconstruction. Old `LibraryQualifierContext` admission stays v1-exact. Fresh
v2 admission rebuilds the locked graph, origins, both projections and full source
semantics; comparing a supplied origin table with another supplied table is not proof.
Source-free verification still has original source bytes in the verified context,
so real original-source reconstruction remains required. Existing source/context
and trusted `linked_source` identity checks stay mandatory.

## C: compiler admission and existing execution

Compiler changes should be confined to exact context/capability/profile negotiation,
the existing `linked_source` verification path and capability projection. After
producer and consumer admission, the builder removes **only M** as a consumer proof,
using the same ownership pattern as A2's R removal. Existing Q and runtime/nominal/
varip capabilities remain unchanged. Do not add M to the PineLib manifest or Session.
USER_METHOD emission already follows exact declaration IDs and evaluates its receiver
once. No emitter change is proposed without a generated failing case.

Keep dependency hashes for every exact locked revision and `@linkage`. Source,
context, closure, target, generated bytes and artifact identities remain sealed by
the existing artifact-v3 path. A changed method body, default, export status,
receiver type, import revision, alias resolution or selected overload must invalidate
the affected artifact/checkpoint compatibility; no state migration or latest fallback.
Restore must receive the already admitted immutable nominal registry before callbacks,
exactly as today.

## Required proof before claiming B/C acceptance

The 72 frozen source rows are the first seed, not complete edge coverage. Run both
v5/v6 and preserve every old test/assertion. The following additional protocol and
lifecycle cases should be authored as independent fixtures before implementation:

* Public int/float and same-receiver ordinary-parameter overloads; named arguments
  reordered, required/default omitted, duplicate/unknown names, wrong receiver and
  argument types. Check source argument IDs/default bindings, not only output 5.
* Direct and internal transitive imported-method calls; two aliases of the same
  publication; two same-named nominal types from different refs. Keep namespace,
  transitive-root and indistinguishable-priority questions separately UNVERIFIED.
* Private helper called internally, identical private names in two source modules,
  root attempts to invoke each private helper, local same-spelled method collisions,
  and builtin/user coexistence. Check no synthetic fallback bypasses visibility.
* Public receiver/ordinary/default/return privacy, including unused exported methods;
  enum/UDT fields and nested collections. Reject capture of nonconst library globals,
  while literal global constants remain usable by proper source scope.
* Explicit simple positive/series negative, absent receiver qualifier with the admitted
  export floor, v6 ignored reference annotation and the unchanged v5 UNVERIFIED
  reference profile. Verify actual result types/qualifiers and metadata rejection.
* Two written method callsites produce `[11,22,33]` when packed as `a*10+b`; receiver
  mutation occurs once, with manual traces `[1,2,3]`; shared array identity packs as
  `[11,22,33]`. Add realtime nonfinal/final rollback, abort/retry, loop contexts and
  checkpoint cuts before/after the first committed method call. Match exact literal
  trace and full JSON continuation, not a checkpoint equality alone.
* UDT ordinary field versus declared-varip field, enum membership and nested handles;
  immutable registry admission before restore. Compile and execute through the actual
  DI factory, without callback-learned declarations or runtime-manifest changes.
* Fully resealed tampering: private->public, receiver/overload row substitution,
  omitted exported callable, invented row, moved call source span, swapped origin
  scope, deleted import edge, alias/revision substitution, marker/context stripping,
  context-schema/profile downgrade and colluding qualifier/call facts. Reconstruct
  against actual locked bytes and preserve the wholly replaced-input authentication
  limitation rather than claiming hashes authenticate an author's original program.
* Original genuine 1.0/1.1 with AST2.0/2.1, new 1.2 with AST2.0/2.1, old/new producer
  and consumer pairs, unknown revision combinations, exact capability sets and
  consumer-only capability removal. No TypeError retry or weakened receipt verifier.

Suggested source split: B1 internal origin inventory/shared selection projection;
B2 exact v2 context/schema/floor admission; C compiler negotiation and normal
generated lifecycle proof. New tests/docs form separate frozen groups; any old
setup correction needs its own before evidence and review. The active A2 snapshots,
manual corrections, old denominator and all neighboring scalar/runtime work remain
untouched. No full owner suite was repeated for this read-only design.
