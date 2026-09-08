# RC6 bounded matrix operation evidence

The original 25-row literal table remains byte-identical, SHA-256
`8d5e61ff844ebb7c67f87f50f199b1621b08c1523cee9068ebb152c661370be9`.
Its v5/v6 namespace/method expansion contains 100 source cases and 20 operation
tuples: `matrix.get`, `set`, `rows`, `columns` and `copy`. The corpus hash is
`sha256:eb35310eb50a15898d2bd96ba610120ecc3c314b300f870e0e0cccf3f54cda81`.
Sources and expected snapshots were written before generated execution; no
runtime or compiler produced the expected values.

The independently read [v5 matrix documentation](https://www.tradingview.com/pine-script-docs/v5/language/matrices/)
and [current matrix documentation](https://www.tradingview.com/pine-script-docs/language/matrices/)
support zero-based coordinate access and replacement, explicit dimensions,
ordinary var persistence and primitive-element copy independence. The copied
primary-rule receipt identifies the applicable sections and retained exclusions.
The table includes rectangular, one-row, one-column and 0x0 shapes; integer,
float, bool and string cells; canonical float NA cells; and distinct copies with
subsequent edits to both matrices. It does not establish behavior for invalid
indices, missing IDs, non-square zero dimensions, bool NA, reference-valued
shallow copies or capacity limits.

The committed test uses the normal `NativeRC6CompilerAdapter.compile` route.
It checks the returned admitted source call, exact producer binding and version,
typed result, target ABI and generated artifact. Storage observation uses the
adapter's returned semantic declaration facts and source-map locations to read
actual emitted binding literals. It does not recompile the source, guess storage
IDs, replace generated operations or implement matrix semantics.

Bar 0 establishes the authored matrix and scalar state. Bar 1 executes the
primary operation; bars 2 and 3 check persistence or the literal copy followups.
Every snapshot preserves both dimensions, all cells and their exact types,
canonical NA markers, scalar results and copy identity. The observer rejects
dimension transpositions even when cell counts agree, false/zero substitution,
integer/float substitution, wrong cell order and a false alias relationship.
Setup `new`/`set` calls and later copy mutations do not receive separate builtin
assignments.

Five paths with full and compact transcripts produce a planned 1,000 operation
records: the real target ABI plus generated historical, realtime trial/final,
abort/same-sequence retry and JSON checkpoint continuation. The planned full
test denominator is 1,010, including ten provenance and observer controls. These
counts remain intact despite local platform limitations.

Local Windows Native preflights on Py3.11 and Py3.13 each preserve 199 passes and
one initial `No module named 'fcntl'` failure among 200 selected checks. The
failure occurs during host capability import before matrix execution. Subsequent
imports happen to proceed in that process; this cache effect is not used to
claim a clean Native run or erase the original failure. No `fcntl` shim or
production workaround is installed. Formal Native validation remains pending
on Linux.

A separately named ignored control runs `build_consumer_bundle` followed by
`compile_consumer_bundle` and the same runtime lifecycle observations. Its
records explicitly say `execution_route=direct_consumer_compiler_runtime` and
`native_adapter_verified=false`. This direct component evidence does not replace
the committed Native tests or satisfy their pending platform gate. The two routes
and their source hashes were recorded before direct execution.
Both local direct-control runs pass all 1,010 checks with no failures, errors or
skips. Each retains 1,000 operation records, 4,000 complete final snapshots,
1,200 realtime trials and 800 JSON restores. All 100 literal cases match in
every direct-control path/transcript combination. These results do not change
the preserved Native failures or its Linux-pending status.

The isolated host baseline is `4d952ccac1f051bd75e7a4ef2dd7644cdf379b83`.
All seven dependency repositories are copied and indexed before execution:
the three core source trees match reviewed assembled index
`381d56258610ab04e6a0622602ee479ded146010d2bf51f8589c74787a043274`, while
contracts, backtest engine, marketdata provider and optimizer come from the exact
Git pins in that baseline's lifecycle source file. The complete local index is
`72099d1332b253b43128f7065afd6912aa351e28ea28dc008e0f43735e9a20cd`.
Every local observation is labelled a frozen-content snapshot rather than a
published-pin run. Formal CI reads its actual declared dependency pins.

No previous corpus, test, source owner, inventory, progress state or dependency
pin changes in this package. The earlier matrix ABI observations and Native
preflight failures remain separate immutable evidence. Full Stage 2, complete
matrix conformance and TradingView execution parity are not claimed.
