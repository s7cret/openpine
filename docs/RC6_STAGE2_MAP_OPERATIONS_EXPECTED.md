# RC6 bounded map operation evidence

The original manual table remains byte-identical, SHA-256
`3afd6e3a0a1a0abe972c40190bff3dd2caf566aa5e7a7a355aa9d2acd5bb9ffe`.
Its 28 independently authored cases expand to 112 sources: Pine v5/v6,
namespace and method calls. The eight operations are `put`, `get`, `contains`,
`remove`, `size`, `clear`, `put_all` and `copy`, giving 32 version/form tuples.
The literal corpus hash is
`sha256:ee83c55fb9a562342b6ba7f2bbc7c05b27bf77ab2f3f0dfeec0614cd0775c1b6`.
No expected value is calculated by the runtime, compiler or a duplicate map
implementation. The table records primary-source derivation, not TradingView
execution.

Bar 0 performs explicit declarations and authored input setup. Bar 1 executes
the primary operation. Bars 2 and 3 verify persistence or the authored mutations
to the copied map and its original. Each snapshot contains every key/value pair
in order, scalar returns tagged as canonical NA or a typed value, and the exact
same-reference relationship when two map variables exist. The observer never
converts ordered pairs into a Python dictionary. Typed comparisons distinguish
false from zero, integers from floats, missing values from null, and equal-sized
maps with different contents or insertion order.

The actual target ABI and four unmodified generated execution paths run with
both full and compact transcripts. Generated paths cover historical callbacks,
realtime trial/final callbacks, abort with the same callback sequence retried,
and JSON checkpoint continuation. Abort restores complete ordinary map contents
and preserves the successful transcript and sequence; checkpoint continuation
preserves contents and identity before the next source callback. This yields
1,120 operation records, 4,480 final snapshots, 1,344 realtime trials and 896
restores when all cases complete.

Source admission and compilation follow the normal consumer-bundle pipeline.
The test selects the primary source call by its exact source location and checks
the producer symbol, overload, call form, typed return, supported Pine version
and ABI callable. Storage observation uses admitted global declaration IDs and
their generated source-map spans to locate literal runtime binding IDs. It does
not guess identifiers, rewrite generated operations or calculate Pine results.
ABI controls invoke the same existing public owner adapters; setup and followup
helpers do not receive independent builtin assignments.

The broad key metadata on `get`, `contains` and `remove` is a separate remaining
contract limitation. The local target still describes `key` as `string` while
the language supports declared map key types. Each report records the actual
target parameters and key types exercised by these cases. Successful integer-key
or string-key observations do not certify the full generic K contract. Missing
bool-valued get/remove results, NA keys, reference-valued shallow copies, resource
limits and whole-map-family conformance remain outside this corpus.

Formal runs read the dependency pins in `docs/RC6_LIFECYCLE_SOURCES.json`, with
normal CI independently verifying their source identities. Local validation uses
an explicitly labelled frozen-content snapshot, rebuilt from Git bases and
immutable phases indexed by
`381d56258610ab04e6a0622602ee479ded146010d2bf51f8589c74787a043274`.
The producer base is `7170736f2730316407f0d896447b08e76f5c9c08`, compiler
`95a14be4be8987faafb3e7629c744e698fd9f134`, and runtime base
`d33fdea2ff3d2fa3c567c2c83da08664c8937fc1`. The producer includes the reviewed map
and string source-contract phases. The runtime includes the reviewed comparison,
string, map metadata and independently derived metadata-identity fixtures; its
target bytes have SHA-256
`3ba4d37a722423332aa11485240c6efef586735c3793022f482a4cb8ed6084d2`.
Local execution makes no published-pin claim and leaves the prior host files,
runtime owners, inventories, progress state and dependency pins unchanged.

The isolated Py3.11 and Py3.13 runs each pass 1,130 tests with no failures,
errors or skips. Each preserves all 1,120 observations and their full snapshots;
all 112 cases match in every path/transcript combination. The forty report files
match byte-for-byte across the two interpreters. A prior 224-check preflight also
preserves all 112 actual emitted programs and their admission/artifact records.

Assignments belong only to the primary operation's actually admitted DIRECT
tuple and execution path. This bounded corpus does not complete Stage 2 or the
full builtin denominator. Published exact-pin Linux validation is a separate
required gate.
