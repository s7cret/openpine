# RC6 cleanup, bounded results and execution progress — 2026-09-05

## Verified publication

Baseline: `1e051ae7e54de7808826e6823bcc8cb66cf26404`.
Published runtime head: `ff718ec6ef5b78732abd463b0c4c64e2c8560e3d`.
Runtime tree: `0f3d67384659b5e4f68f6610de8fbc7dc11680df`.
Integrated CI: https://github.com/s7cret/openpine/actions/runs/33982457446

The six source commits were reconstructed with per-blob, tree and commit SHA checks.
Both interpreter jobs passed before a separate publication job advanced RC6 without force.
The temporary publication branch was archived as tag `ops/rc6-cleanup-20260905` at
`647526ca416436a3c1dbaaa55acf7261acaf749e`, then its branch ref was removed.
The publication receipt again contains exactly the four kept branches; main and historical
releases retain their previous SHAs. Sibling repositories were not modified in this pass.

| Commit | Scope |
| --- | --- |
| `ce81018904ca4bf04c97d00fd7aaac2ba484a248` | refactor(OP-35): remove format/logging fallbacks and retired RC6 maintenance code |
| `12f9d9b37b582faddbbb7434f53e0f3a1e9b7e1c` | fix(OP-06): stream sealed bulk results with bounded frames and verified identities |
| `99bf4d8f2a9caefc354168f2391aaa344d775644` | fix(OP-03): retire duplicate adapter config conversion and preserve rounding semantics |
| `79da8980da181dc46b13b0b56c33c130303d22e8` | fix(OP-06): reject nested MessagePack extension objects and excessive result depth |
| `e6930055e54358e7f3a25a902216e3f42009ab05` | feat(OP-25): deliver validated progress and serialize bulk inputs once |
| `ff718ec6ef5b78732abd463b0c4c64e2c8560e3d` | test(OP-06): bind optimizer fixtures to the explicit codec sandbox policy |

## Cleanup before feature work — OP-35 / OP-36

See [RC6_CLEANUP.md](RC6_CLEANUP.md). Removed the fake Parquet/logging compatibility
package, historical RC2/RC4 candidate templates, the 4.0 release checklist and the
completed one-shot branch-consolidation script/tests. Production logging now uses
required structlog with real bound context. Parquet callers share one PyArrow module:
atomic replacement on successful writes, failed-write preservation and metadata-only
row counts. No JSON or pickle file is silently treated as Parquet.

The final maintenance commit removes the obsolete apply-reviewed-series workflow and
encoded-series extension helper from RC6, and adds a permanent read-only
`rc6-native.yml` workflow plus an explicit selected-regression inventory. The source
snapshot workflow no longer watches a retired maintenance branch. Encoded publication
submissions and write-capable one-shot workflows remain only in the archived maintenance
tag, not the RC6 runtime source. The existing main/release quality workflows are retained;
this functional CI does not replace their immutable-release or coverage acceptance.

Retained deliberately: SQL migrations/schema normalization, `legacy_4x` broker profile,
working CLI/provider adapters and Pine v1-v6 behavior. These are not dead code merely
because a name is old. No user database or artifact was deleted or rewritten.

### Compatibility notes

Real existing Parquet is still readable. Old JSON/pickle files named `.parquet` are
rejected, even with `OPENPINE_ALLOW_LEGACY_PICKLE_PARQUET=1`; reimport from the original
source. There is no automatic unpickling or destructive conversion.

The worker now explicitly depends on `msgpack`. Rebuild and re-admit the worker policy
using the updated RC6 candidate; an old admitted allowlist is rejected before staging.
Do not disable admission or sandbox checks. The compatible sibling source pins remain
in [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json). Same version strings alone
are not sufficient; the immutable production stack/wheel release task remains open.

## Bounded, integrity-checked bulk results — OP-06

Instead of a single potentially oversized JSON result, the child incrementally sends
primitive-only MessagePack in at most 256 KiB raw chunks. Every chunk has a sequence,
size and SHA256; the final manifest binds the entire payload to the admitted execution
context, effective configuration and applied input hashes. Non-finite float metrics
are preserved without pickle/custom object loading. Duplicate map keys, extension
objects, excessive nesting, oversized, reordered, truncated and mixed-identity streams
are rejected. The parent exposes the verified manifest and still requires a zero worker
exit. Old unsealed bulk-result messages are no longer accepted.

The receive-side encoded spool rolls to disk after 2 MiB, and the total encoded result
has a 256 MiB ceiling. This is a transport bound, not a constant-memory backtest claim:
the broker collections and final decoded result still materialize, and individual large
scalar encodings can allocate before a cap is checked. Existing per-worker memory
limits can be reached earlier. Chunk hashes are integrity checks, not signatures,
TradingView correctness proofs or checkpoint/state-hash proofs.

## Configuration, inputs and progress — OP-03 / OP-25

The adapter reuses its canonical configuration converter instead of duplicating it.
Explicit zeros and the five supported rounding modes survive conversion, including
`none` and `truncate` instead of being silently mapped to `floor`.

Bulk input frames are generated lazily; each candle is JSON-serialized once, with a
bounded UTF-8 frame and exactly one final input batch. Multiline frame injection is
rejected. Progress has a fixed total and integer, monotonic counters, throttles noisy
intermediate updates and preserves initial/actual terminal updates. No missing work is
invented to reach 100%. Computation progress is distinct from verified final success.

The actual isolated processed-bar count is returned instead of the input length. CLI
precompiled/artifact paths now pass inputs and progress. The gateway artifact runner
forwards progress to the existing queue/UI channel without blocking on progress-only
queue saturation. Browser-level UI testing and a UI redesign were not performed.

## Verification evidence

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts (complete functional suite) | 384 | 384 |
| PineLib (complete functional suite) | 198 | 198 |
| Ast2Python (complete functional suite) | 356 | 356 |
| Backtest Engine (complete functional suite) | 513 | 513 |
| Native RC6 and selected OpenPine affected-path regressions | 597 | 597 |
| Total | 2048 | 2048 |

Zero failures, errors and skips in both jobs. These are the same 2,048 cases on two
interpreters, not 4,096 unique tests. Native tests include real protected workers,
optimizer and HTTP paths, actual compiled Pine and broker fills, progress/counter and
manifest assertions. Bubblewrap/AppArmor protections were not disabled. Changed-runtime
Ruff checks, package compilation, wheel and sdist builds passed on both interpreters.
All-test collection was checked locally; the complete OpenPine test suite was not run.
The remaining three sibling suites, coverage thresholds and TradingView oracle comparison
are not certified by these numbers. Local targeted reruns overlap and must not be added.

The initial CI run `33982099440` correctly stopped publication: two real optimizer tests
used a stale shared allowlist that lacked msgpack. Commit `ff718ec` updates only the
fixture, asserts equality with the candidate/runtime package policy, and adds a negative
test showing that the old policy still fails before staging/process creation. No
production admission check was relaxed; both failing tests pass in the successful run.

Downloaded publication and Python 3.11 archives were SHA256 checked, all JUnit reports
parsed, and both Git bundles verified against the local exact reviewed commit history.

| Artifact | SHA256 |
| --- | --- |
| Publication/3.13, `9974188059` | `e1863f26ff694befbcc534d7a7493a8fb6d0a544a2475d327b4d93bc950eeaa4` |
| Python 3.11, `9974184107` | `82e16381ed330f85a3f7cd898e35db802dccf84492fb9928ba5e21b691f3ef48` |

## Scoped performance measurement

A local Python 3.13.5 microbenchmark compared the old and new input-frame construction
with 20,000 producer-created repeated envelopes (19,480,945 output bytes), after warmup,
seven alternating runs each. This is serialization only: no I/O, broker, compilation or
dataset admission. The repeated envelope is not a valid executable chronological dataset.

Median old/new: 0.2384 / 0.1335 seconds, a **1.79x framing speed ratio** in this environment.
Peak additional tracemalloc allocation increased from 1,967,571 to 3,642,753 bytes
(about 1.88 to 3.47 MiB). The CPU improvement is not a whole-backtest speedup or a memory
improvement. Shared-environment timing variability and the buffer tradeoff are explicit.
The reproducible benchmark and raw samples accompany the delivered evidence archive.

## Acceptance still open

This pass does not close all OP-03/06/25/35 criteria or all 36 tasks. Remaining work
includes immutable production deployment, explicit full/metrics-only result profiles,
whole-run memory/backpressure/cancellation acceptance, complete request integration and
strategy namespace, real checkpoint/state identity, comprehensive realtime, browser UX
and Pine v1-v6 TradingView oracle coverage. No final production release is claimed.
