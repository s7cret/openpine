# RC6 bounded string operation evidence

This corpus preserves the original string table, SHA-256
`67362aaa9d69bdb9297fbf862a870f4218cfef8493602936f2dca4522df728ac`, and the
separately authored supplement, SHA-256
`7163768f5dacd31dc66a99971d7c9a60e535a1cafddd96c2213c8228c5a020f0`.
Neither table is rewritten using runtime results. The original 34 rows expand
to 63 version-specific cases. Eighteen v6 supplemental boundaries and the v5/v6
NA-trim cases add 20, giving 83 cases and ten namespace version/overload tuples.
The supplement's eight native compatibility controls remain in its original
bytes; they are not promoted into new Pine correctness expectations.

The corpus content hash is
`sha256:07519ab54055f5d6d6b792058d3db8ba6c4829e6c919487dfbc6a7d6bd23ac76`.
Each case has separate source, settings and expected files. Bar 0 establishes
the explicit typed initial result. Bar 1 executes the operation; bars 2 and 3
check persistence. Original operation expectations apply unchanged on bars 1-3.
The observed result records distinguish a canonical NA value from a typed
integer, float or string, including exact whitespace and Unicode codepoints.

Five execution paths run with both full and compact runtime transcripts:
the actual target ABI, generated historical callbacks, generated realtime
trial/final callbacks, generated abort/same-sequence retry, and generated JSON
checkpoint continuation. The resulting 83 × 5 × 2 observations retain separate
numerical-comparison and semantic-authority dimensions. An observation or a
numerical match alone does not establish independent Pine authority.

All 29 original v5 inference labels are retained verbatim in case provenance and
observations. The separately copied independent authority receipt, SHA-256
`e21bf08a0f41341edcc47c8c5d70ab4c8ac9ef73150d3969c91d7aaea3010f6b`, supports
24 of these cases using the exact v5 reference and translation clauses. Five
remain unverified: `tonumber-7` through `tonumber-11` cover underscore, exponent,
surrounding-space, `NaN` and `Infinity` inputs. The latter two matching numerical
observations do not establish their source semantics. The first three cases'
original NA expectations are not replaced with current native compatibility
values. The test records these cases as unverified observations without skip or
xfail; they cannot receive a passing evidence assignment merely because execution
succeeds. Their numerical differences remain visible in the complete corpus
comparison. A separate primary-authority mapping controls whether any originally
inferred v5 case can receive an assignment; original labels are never erased.
This leaves 78 authority-eligible cases and five authority gaps. Across ten paths
and transcript combinations, all 830 records remain present: 780 may be assigned
to the ten directly executed tuples, while 50 remain unassigned. Numerical
comparison is separate: 80 cases match the literal table and three differ on
the reviewed compatibility snapshot, yielding 800 matches and 30 unresolved
differences. No TradingView execution is claimed by the source-derived table.

The unmodified generated program executes through the normal producer bundle
and compiler artifact pipeline. The observer obtains scalar storage identifiers
from admitted global declarations and the corresponding generated source-map
locations; it does not guess generated identifiers. Input literal facts must
equal the original input codepoints before compilation. A Pine-specific literal
encoder preserves form-feed and vertical-tab as actual source characters because
Pine and JSON do not share every string escape. Source-map lookup is an
observation adapter, not an implementation of string operations.

The source calls use a single positional argument. At the reviewed local source
snapshot, the `str.tonumber` parameter spelling differs from the primary
reference's `string` spelling. This corpus does not certify named-argument
behavior or fix that separate producer/target contract issue. Coverage
assignments are limited to the exact operation, version, overload and execution
path under test; declaration and setup behavior receives no builtin assignment.

Formal runs read component pins from `docs/RC6_LIFECYCLE_SOURCES.json`; the usual
CI admission verifies those identities. The local launcher instead records a
frozen-content snapshot: exact host baseline
`4d952ccac1f051bd75e7a4ef2dd7644cdf379b83`, producer
`7170736f2730316407f0d896447b08e76f5c9c08`, compiler
`95a14be4be8987faafb3e7629c744e698fd9f134`, and runtime base
`d33fdea2ff3d2fa3c567c2c83da08664c8937fc1` plus immutable string bundle
`4386cbbe15ebea56320ff187cbb1d201c957b173dea8513cd694eaf26fee6b03` and the
separately reviewed metadata-identity fixture bundle
`3560183a376e5960d764de98afc4c8d561d2e374f890dae444e86c5300fd2cea`.
Local observations explicitly make no published-pin execution claim.

The isolated Py3.11 and Py3.13 runs each pass 848 tests with no skips: 830
operation/lifecycle checks and 18 provenance, typed-observer and adversarial
authority-ledger checks. Each run records 3,320 final events, 996 realtime trials
and 664 JSON restores. The complete comparison remains 80 of 83 literal cases,
while every authority-eligible assignment passes. Published exact-pin Linux
execution remains a separate required gate.

The whole corpus denominator remains visible, including unresolved expectations.
Non-BMP length, ambiguous decimal rounding and overflow/underflow behavior,
unverified v5 character-set rules and complete string-family conformance remain
outside this bounded evidence. Full Stage 2 remains in progress. Old host files,
tests, production owners, source pins and inventory are unchanged by this corpus.
