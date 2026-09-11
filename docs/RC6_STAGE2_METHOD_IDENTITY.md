# Stage 2: preserve library method selection and lexical mutation identity

This repair continues the existing source-bound method candidate, not a new
interpreter or a claim of full Stage 2 completion.

## Reproduced problems and owner fixes

The scoped preview correctly chose a public float-argument method, but its private
int-argument overload shared the generated name. Final analysis could rebind the
call to that more-specific private declaration. A literal test produced 1003 where
the public declaration requires 103. Projection now assigns a unique private name
to each source-bound method declaration. Receiver/argument evaluation, callsite state
and source provenance remain owned by the existing compiler/runtime.

A second bug associated reassignment with a bare name across every scope. Updating
local n in a method could incorrectly strengthen an unrelated global n=2 to series,
rejecting a valid simple receiver or const input default. An iterative lexical
prepass now records declaration identities, preserving source order and shadowing.
Ordinary checking and call-specific qualifier inference use the same classifier.
Real outer assignments remain mutations; parameters, loop and tuple bindings do
not contaminate an unrelated declaration. Legacy v1/v2 predeclaration rules remain
unchanged. No general series-to-simple coercion is introduced.

## Required evidence

60 added cases: 38 producer, 10 compiler and 12 host. No prior test ID was removed.
The fixed inventory is 20586, including existing accounting/infrastructure checks;
it is not a TradingView compatibility percentage. Four new protected-worker cases
are mandatory in addition to the original four imported-method worker cases.

The broker fixture keeps public and private state separate: on the third bar the
public counter is 103 and the internal private counter is 1003. It submits qty=3
only when both are correct, filling at 102 on close or 103 at the next open. Pine
5/6, both transports, named arguments and fill recalculation are covered. Separate
compiler cases check checkpoint equality. Expectations are hand-derived, not TV
exports. Old tests were not removed or rewritten to manufacture these values.

Local Python 3.13 complete suites: Pine2AST 2989, Ast2Python 1321, zero failures,
errors or skips. An initial environment lacked installed packages/build tooling;
it was replaced by actual offline wheel installation, not by disabling isolation
tests. Full new Linux Python 3.11/3.13, sandbox, architecture/corpus, frontend and
build acceptance must be observed before RC6 merge. Prior candidate run
34632849181 passed, but did not contain these repairs and is not their acceptance.

## Compatibility and remaining scope

Recompile linked artifacts with the coordinated source pins. Generated symbol
identity changes intentionally with this producer repair; checkpoint/source hashes
must not be ignored. Existing library lock semantics and the pinned exact source
checks remain intact.

Custom methods in ordinary/namespace call syntax, cross-version imports, the full
versioned catalog and complete independent builtin evidence remain outside this
repair. Full Stage 2 remains in progress. No measured speedup or external
TradingView equivalence is claimed.
