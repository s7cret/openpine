# OP-08: reconciliation of nested requests with published RC6

## Preserved sources and scope

This work continues OpenPine `220285905ac994564c3a70d0af264dce3bb9f288`,
including published checkpoint v2 receipts, exact worker capability negotiation,
static request admission and numeric-underflow rejection. It does not replace
RC6 with the older local OpenPine tree `c298f384eee8`.

The compatible original local library commits are preserved without rewriting:
PineLib `c187e7c531938f71b36587bcd917ff8f8bca4ce2` and
`cddaa0bf2324db36b4cd29e944a251032658c59d`; Ast2Python
`51552e7c1de339d8de4794290655e574d6447e82`. The exact complete source set is
`RC6_LIFECYCLE_SOURCES.json`. Recompile modules and update this source set together;
same version strings are not proof of identical code.

## Nested expression execution

The compiler emits checked child methods under the script's dynamic-request
policy. PineLib routes their nested requests through the outer RequestEngine,
sharing parent identities, cache, depth/resource budgets and transaction rollback.
An ephemeral context is bound to the exact child transaction and reset even on
failure. Only explicitly declared canonical instrument IDs and venue ticker IDs
are aliases; conflicting aliases fail independent of insertion order.

Preflight now traverses generated child methods under their enclosing request's
symbol/timeframe. Empty arguments inherit that context, not the chart's context.
Nested `security_lower_tf` is compared to its immediate parent interval. Dynamic
contexts stay unresolved; an ignored invalid interval is not assumed to require
an otherwise unused source before its parent timeframe is known.

Tests use different chart/requested prices to drive real broker orders, compare
bulk/interactive intent tapes, verify future-only data changes do not alter earlier
no-lookahead decisions, and check depth-limit rollback. A saved nested request
cache continues through the already published checkpoint v2 receipts with an exact
intent suffix and final-state match. This does not implement full broker/IPC resume.

## One public data API, bounded preload transport

The supported host input remains:

```python
config.request_manifest = build_request_manifest(execution_context, datasets)
```

Dataset descriptors and original canonical FINAL envelopes have the same meaning
as in `RC6_REQUESTS_CHECKPOINT_BLOCK.md`. No parallel `request_sources` model or
silent manifest conversion is added. Automatic discovery/downloading from
UI/CLI/gateway remains a separate task; this module grants no worker network access.

Before starting the worker, the host serializes a detached manifest to a spooled
buffer. Bootstrap carries its descriptor rather than the entire data JSON. The
host then writes numbered chunks; the child reconstructs and revalidates the
original manifest and effective configuration before HELLO or generated execution.
Each chunk and the complete stream are hashed. Unexpected order, duplicate JSON
keys, bad lengths/hashes, truncation, foreign execution identity and changed
configuration fail before publishing the restored configuration.

Limits are implementation safeguards, not TradingView account limits:

- Up to 128 KiB of raw data per chunk, with a 256 KiB encoded-frame ceiling.
- Transport spool rolls over at 2 MiB; total encoded preload ceiling is 64 MiB.
- At most 64 datasets and 250,000 bars in aggregate across all datasets.

Existing worker memory and temporary-filesystem limits can bind earlier. In
particular, a disk-backed spool inside the sandbox still consumes its bounded
private temporary filesystem. A 64 MiB transport ceiling is not a guarantee that
every worker configuration can run a 64 MiB preload. Source lists, JSON decoder,
request caches and broker output can still materialize: this is not constant-memory
execution, end-to-end backpressure/cancellation or a measured speedup.

The public manifest format is unchanged, but the bootstrap wire format requires
matching host/worker sources. Old and new workers must not be mixed. Aggregate
bar limits are now checked across datasets instead of separately per dataset only.

## Verification boundaries

New tests include two mandatory actual isolated-worker cases with nested requests
and a valid multi-frame, production-chunk-size preload. Unit cases separately
exercise corrupt streams, spooling, exact configuration identity and bounded
framing. Existing native, checkpoint receipt, preflight, capability, numeric and
selected application regressions remain mandatory. Source pins, original commit
identities and actual CI results are recorded in the publication evidence, not
inferred from the existence of this document.

The broader acceptance remains open: UDF and unsupported mutable/local capture,
live/revised requests, FX conversion, nonstandard ticker/session transformations,
automatic dataset loading, complete strategy exits/trailing/risk/indexed trades,
full isolated-job restart, production wheel admission, browser UX, coverage and
external TradingView oracle parity. No benchmark was run for this merge.

Semantic reference (not execution-oracle evidence):
https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/
