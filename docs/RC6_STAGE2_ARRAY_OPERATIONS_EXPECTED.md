# RC6 bounded array operation evidence

The corpus `verification/builtin-array-operations-v1/manifest.json` preserves the
39 independently authored literal rows whose original bytes have SHA-256
`556d6958a9a77cbffda0374cbf24bdca10dcaf7ddbdea8a853edd53f74bccc6b`.
No runtime output supplies an expected value. The source table records primary
Pine v4, v5 and v6 array documentation and identifies its unverified boundaries.
Its lists, scalar results and copy/slice alias transitions remain unchanged.

Namespace calls expand to 113 cases in the table's declared versions; method
calls add 78 cases in v5/v6. The resulting 191 cases cover 89 exact version,
symbol, overload and call-form tuples across 18 operations. Only the operation
under test receives a coverage assignment. Constructors, setup pushes and the
authored follow-up `set` calls do not gain coverage from these tests.

The corpus content hash is
`sha256:fcdb431f78102e7a09595bbf9660cb502145de797c5c637ba968dffece990a4c`.
Each case contains separate source, settings and four-event expected files.
Bar 0 initializes persistent arrays; bar 1 performs the target operation;
bars 2 and 3 perform the table's copy/slice follow-ups, or verify persistence.

The new host test runs the exact target ABI and four real generated-code paths:
historical, realtime trials, aborted same-sequence retry, and JSON checkpoint
continuation. All five paths run with full and compact runtime transcripts:
191 cases × 5 paths × 2 formats = 1,910 observations and 7,640 final bar events.
The realtime paths add 2,292 trial observations. The rollback and checkpoint
paths perform 1,528 JSON restores, including restoration after bar 1 before
the alias mutations. Abort checks preserve the previous values, successful
sequence and transcript; the retry uses the aborted attempt's sequence.

Observations contain every array element, typed scalar results, and reference
identity equality. Strict recursive comparison distinguishes `false` from zero,
integers from floats, element order, and copied versus shared references. Direct
ABI calls also check `None` for void results. Generated void calls are checked
against the producer's admitted void return type. Storage identifiers come from
admitted global declarations and their source-map-linked literal arguments in
the generated Python AST. This adapter only locates state; the unmodified
generated program performs all operations, and the public runtime reference
owner supplies array payloads.

Two v6 binary-search tuples remain unassigned. Their complete installed catalog
contracts include optional `sort_field`, while their ABI binds only the array
and value. The unchanged static surface reports `UNAVAILABLE` with exact reason
`A2P_PINELIB_SOURCE_PARAMETER`. Four cases still execute and compare successfully
in every path/format, but this proves only those calls without `sort_field`.
The reports preserve all 191 cases and all 89 tuples, assign 187 cases to 87
currently direct tuples per path/format, and emit a closed gap ledger for the
remaining four cases/two tuples. The scanner and its denominator are unchanged.

Formal execution reads the component pins from
`docs/RC6_LIFECYCLE_SOURCES.json`; the usual CI source verification must establish
those identities. Local validation uses a separate launcher with exact Git
source snapshots (producer `7170736f2730316407f0d896447b08e76f5c9c08`, compiler
`95a14be4be8987faafb3e7629c744e698fd9f134`, runtime
`d33fdea2ff3d2fa3c567c2c83da08664c8937fc1`) and explicitly labels its observations
`verified_git_source_snapshot`, with no published-pin execution claim. The
launcher does not alter committed test imports or dependency metadata.

The first local run passed the value tests and failed report teardown when it
incorrectly required all 89 tuples to be direct. Its logs and source snapshot
are retained as evidence of the discovered contract gap. The explicit ledger
is reporting metadata, with negative controls for altered reasons, identities,
status, missing/duplicate rows and extra fields; it does not relax value tests.

This is bounded manual evidence, not a TradingView export or whole-family
acceptance. Negative indices, NA elements/missing IDs, invalid removals, parent
shrink effects on slices, nested reference-element copies, binary-search
duplicate choice and full Stage 2 conformance remain unverified. Existing
neighbor-search fixtures, old tests and production code remain untouched.
