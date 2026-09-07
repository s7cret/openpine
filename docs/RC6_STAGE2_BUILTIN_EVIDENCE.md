# Stage 2: versioned builtin bindings and independent examples

This block remains partial Stage 2 work. It does not accept the complete catalogue,
the stateful language matrix, imports, or the complete independent builtin corpus.
Stage 3 must not start on the strength of this block alone.

## Ownership and implementation

`pine2ast` owns scalar overload identity, source argument names, version availability,
return types and qualifiers. `pinelib` owns numerical behavior, coercion and explicit
modern/historical ABI projections. `ast2python` owns exact target resolution and the
public structural binding audit. OpenPine joins these owned contracts into reports;
it does not implement another Pine type system, evaluator or mathematical library.

The parser changes cover audited round, abs, ceil, floor, exp, sqrt, pow, RSI, float
and nz contracts. Runtime changes include ties toward positive infinity, integer
one-argument round results, explicit-precision float results, mintick rounding and
canonical float coercion. Historical aliases are an explicit versioned target table;
neither namespace stripping nor a guessed canonical overload enables a call.

Capabilities and the new builtin surface report require the exact producer overload,
call form, version and ABI mapping. An existing Python callable is insufficient.
Mappings that cannot be established by the compiler's structural audit remain
UNVERIFIED; definite missing or invalid mappings remain UNAVAILABLE. The report
denominator contains every installed frontend callable signature across Pine 1–6,
including unavailable signatures. It is not a claim that the installed catalogue
already contains the entire official language.

Independent review also caught two false-positive report paths. Historical names
now require the same frontend symbol ID as their declared target. Runtime values
use the compiler's narrower value-binding audit, so a call-only injection such as
`SOURCE_SPAN` cannot certify the transaction argument of `close`. Separate negative
tests preserve both reproductions.

## Independent expected values

`verification/builtins-v1/manifest.json` contains 47 manually authored examples.
The expected files use elementary arithmetic and explicit SMA/WMA/RSI derivations;
they were not produced by running OpenPine. The sealed corpus hash is
`sha256:0c2ba86607654abd0eb34d7726272005c73f0f0d0b738efd7a630ed0664820a0`.
Each example runs through direct ABI, compiled historical, compiled realtime,
abort/retry rollback, and JSON checkpoint/restore paths. Each path uses its own
runtime session. Realtime trials use different source values to expose leaked
intrabar state. Compiled artifacts are reused to expose generated-class state leaks.

Evidence records the actual source overload and call form, Pine version, compiler
target hash, catalogue hash and execution path. Numeric trace comparison is labelled
as value-only. The separate execution-evidence gate fails if the same values are
relabeled as another path, binding or target. Empty assignments never pass that gate.
The report always preserves missing signature evidence and never sets full builtin
or TradingView acceptance from this small corpus.

The CLI entry points are `python -m openpine.verification builtin-surface` and
`python -m openpine.verification builtin-expected`. The latter requires the frozen
corpus, its expected hash, actual observations and exact evidence assignments.

## Failure history and test corrections

The initial historical host probe had 28 passes and 19 failures: three missing
precision overload bindings and sixteen unavailable historical forms. These drove
the owner fixes. A later run had 104 passes and 137 target-manifest mismatch failures
because the runtime manifest changed after compilation during that run. This was
the correct identity guard; no admission check was relaxed. The next run uses one
frozen target manifest:
`sha256:739bcc3e7167e49ecbb1f37c76b4fdb4763fad724364ba9ab1cebc989e17d9aa`.

Library receipts preserve their independent failures and focused validation:

- pine2ast: `docs/STAGE2_SCALAR_SIGNATURE_REVIEW.md` and its baseline JSON;
- pinelib: `docs/STAGE2_BUILTIN_BINDINGS.md`;
- ast2python: `docs/STAGE2_BUILTIN_BINDINGS.md` and its baseline JSON.

Changes to existing tests have separate commits. Legacy nz named inputs were
corrected to documented `x`/`y` without changing their asserted types. The old
rounding assertion for -1.25 at precision 1 was corrected from -1.3 to -1.2 using
the documented tie direction. No existing test nodeid was removed or skipped.
Draft new MACD examples that assumed an unverified warmup were not used as an
oracle; final examples use an explicit common constant prefix and document that
modern EMA/MACD seeding remains unresolved.

## Validation and remaining scope

Local focused checks use Python 3.11 and 3.13. Windows cannot establish the Linux
worker contract. All 247 host cases passed on both interpreters before the final
report-label refinement; its eight affected evidence tests passed again on 3.13.
The matching-evidence positive control also passed; all five saved 47-example
observation sets pass the refined execution gate. The final builtin test file has
248 cases. An additional capability file has 27 cases: 14 pass locally on both
interpreters and 13 report the required `fcntl` import error. These are mandatory
Linux cases, with no platform skips. Compiler value audit adds 26 cases to its
previous 122; the final proposed compiler inventory is 832.
The host matrix required about 14 minutes on this Windows machine. The native job
timeout is increased from 25 to 40 minutes to accommodate the expanded full suite;
all test and acceptance commands remain unchanged. The authoritative next gate is
an explicit inventory proposal
against the published 5842-test nominal baseline, followed by the full permanent
Linux checks at immutable source SHAs, real AppArmor/bubblewrap workers, both
Python versions, unchanged Stage 1 corpus, frontend tests and distributions.
Collection alone is not a passing test result. The final publication receipt will
record the reviewed inventory and completed joint run separately.

The corpus does not establish a full builtin matrix, all na/type/qualifier/history
edges, TradingView execution parity, modern EMA seeding, complete library methods
or mixed-version imports. Stage 2 remains IN_PROGRESS.

The preceding nominal branch cleanup is independently recorded in
`verification/nominal-cleanup-receipt.json`; its release refs were unchanged.
Original local outcomes are preserved in `verification/builtin-local-receipt.json`.
The initial source audit and subsequent value-binding delta are preserved
separately in `verification/builtin-source-review.json` and
`verification/builtin-value-binding-review.json`. The CI generator review is
`verification/builtin-ci-generation-review.json`.

## Reviewed Linux inventory

[Collection run 34158391385](https://github.com/s7cret/openpine/actions/runs/34158391385)
completed successfully for Python 3.11 and 3.13 at candidate
`62b7b19491d8a20dfebca23546ca110becbfcc6a`. Both proposals are byte-identical:
SHA256 `0485ff3eb933c58e3bf1df57a9fd7e7b93ddcb1d8245bdb62de727478f80d09a`.
All 5842 baseline nodeids remain; 903 new nodeids produce 6745 mandatory tests.
There are no removals or additional deselections. The provider's five existing
external live-network deselections remain a separate policy.

| Component | Baseline | Reviewed candidate |
| --- | ---: | ---: |
| openpine-contracts | 557 | 557 |
| pinelib | 483 | 591 |
| pine2ast | 735 | 1107 |
| ast2python | 684 | 832 |
| backtest_engine | 1102 | 1102 |
| optimizer | 281 | 281 |
| marketdata-provider | 601 | 601 |
| OpenPine | 1399 | 1674 |

`verification/builtin-inventory-review.json` records the exact added nodeids,
source archives and Python artifact hashes. This accepts the new inventory only;
full execution and Stage 2 acceptance are still pending.
