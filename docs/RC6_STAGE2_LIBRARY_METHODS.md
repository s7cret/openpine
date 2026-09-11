# Stage 2 — source-bound public library methods

## Scope

Implement direct receiver-dot calls to public methods imported from pinned,
same-Pine-version v5/v6 libraries. Use the existing ordinary method resolver,
compiler lowering, PineLib state/heap and broker. Do not add an interpreter or a
runtime delegation backend. The new linker profile is `same_version_methods_v5`;
its suffix is the profile revision, not a claim that only Pine v5 is supported.

Pine2AST performs a typed preview with source-unit visibility, resolves exact
method declarations, then projects the chosen declaration/member tokens to
canonical private names. Receiver and argument expressions, order, written call
sites, defaults and bodies are not replaced by generated wrappers. Existing
scalar/array/reference import paths without methods retain their old format.

## Visibility and typing

Private methods remain in their defining source unit. Public methods become
visible through a direct import, not merely a transitive dependency. A library can
call its private helper and directly imported public helper; those declarations
cannot leak into an unrelated consumer. Same-named nominal types stay distinct.
Receiver types, overloads, explicit qualifiers and public result qualifier floors
are checked with the ordinary SignatureResolver. An exported constant-looking
method result cannot silently become a const input default.

A versioned `pine2ast.library_qualifier_context.v2` retains source and projected
identity for functions and methods. It is re-derived from exact locked sources
before admission; a new explicit `library_method_projection_v1` feature prevents
older consumers from treating the projection as an ordinary unproven bundle.
Ast2Python recognizes the compiler feature without claiming a new PineLib runtime
capability. Provenance and virtual/source span mapping survive token projection.

The producer now rejects direct and indirect recursive call cycles after exact
binding, including function/method cycles. This does not ban distinct overloads
merely because they share a spelling. The call-graph traversal is iterative;
it does not add recursion proportional to user call-graph depth.

## Execution covered

Tests include scalar, array/map/matrix, enum and UDT receivers; typed overloads;
default and named arguments; chaining and void methods; private helpers and
transitive imports; written-callsite state and loop reuse; argument evaluation
order; SMA state; realtime rollback and per-field varip; checkpoint and source
identity rejection. Expectations are independently hand-derived engineering
fixtures, not recorded TradingView executions.

A broker scenario calls imported methods on two Counter objects with steps 1/10,
then shallow-copies the first and adds 100 to its ordinary field. On the third bar
expected ordinary values are 3, 30 and 103. It sends an actual entry of qty=3 at
102 for process_orders_on_close, or 103 at the next open. Direct and imported Pine
5/6 variants run through both transports and calc_on_order_fills. Four additional
tests require real protected worker processes; no library files are given to them.
The generated-session checkpoint cases omit the broker-state guard because that
helper intentionally supplies no broker state; real broker cases keep the guard.

## Candidate verification, not yet joint acceptance

Local Python 3.13: complete Pine2AST 2,951 passed; complete Ast2Python 1,311 passed.
The new host suite has 22 passes and four failures explicitly requiring Bubblewrap,
which is absent locally. No assertion or sandbox policy was disabled. Earlier
local package/import-environment failures were fixed by installing actual built
wheels, not by weakening import-isolation tests. Logs are retained in delivery.

103 new cases: 41 producer, 36 compiler and 26 host. The candidate fixed inventory
is 20,526 and contains every previously admitted 20,423 ID. Collection-only receipts
prove membership, not execution. The unchanged permanent RC6 workflow collects
these cases automatically. Its new joint run on both interpreters, protected
processes, old manual corpus/architecture gates, frontend and builds is mandatory.

## Explicit remaining limits

Same-language-version imports only. This block accepts receiver-dot method syntax;
namespace-spelled custom method calls are not implemented by it. This is an
OpenPine limitation, not a statement that Pine forbids such a form. Full versioned
catalog, remaining broader overload/import contexts, complete builtin numerical
coverage and the other original Stage 2 criteria remain open. A BOUND chain and
an engineering fixture are not external TradingView verification. Full Stage 2
remains in_progress. Recompile with coordinated producer/compiler/host versions.

Static preview adds compile-time work only on the method-import path. Visibility
intervals and candidate scopes are indexed; no end-to-end speedup is claimed.

Primary semantic references (not external execution evidence):
- https://www.tradingview.com/pine-script-docs/concepts/libraries/
- https://www.tradingview.com/pine-script-docs/language/methods/
- https://www.tradingview.com/pine-script-docs/language/user-defined-functions/
