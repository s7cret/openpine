# RC6 review progress — 2026-09-05

This ledger records verified partial implementation, not completion of the 36-task review.

## Published code and verification

Base: `b4621601b91d4186ae62b3f53245e67642264581`.
Verified code head: `ca6c47487a2e6196933c93b27ce866ecc76b7ace`.
Verified tree: `eb3d874b4f0c967ca90025960957be79a7e4e4c2`.

| Task | Published commit | Verified scope |
| --- | --- | --- |
| OP-04 | `370086cd7dcc2d96240c6092d37d00d5b52148b6` | Shared canonical decoder, envelope/value hashes, provenance, numeric/time invariants, preserved OPEN/FINAL, bulk identity/order admission and CLOSED_BAR_ONLY filtering. |
| OP-03 | `96417da08f892b0f048446e546889d3f435476f1` | Lossless configuration transport, effective hash, shared broker/intent settings, close/equity projections for fixed/cash/percent sizing. |
| OP-06 | `ca6c47487a2e6196933c93b27ce866ecc76b7ace` | Actual intent tape and configured outputs; reject incomplete input, failed/early-stopped engine outcomes, invalid counts/intents, and unverified/nonzero worker exits. |

CI: https://github.com/s7cret/openpine/actions/runs/33957666455

Python 3.13: **236 passed, 0 failed, 0 errors, 0 skipped**, including the unchanged real bubblewrap integration test. The sandbox protections were not disabled. This is the native RC6 suite plus selected branch regressions, not the full test suite of all libraries.

Local selected regression: 235 passed without the sandbox case; that case subsequently passed in CI. Additional overlapping local bulk/config boundary run: 96 passed. Do not add these overlapping counts. There are 56 new parameterized test cases in this code series.

Each published file was reconstructed with Git blob SHA verification. CI tested before fast-forward publication. The published code tree was downloaded and compared with the locally tested tree: identical.

Artifact: `reviewed-rc6-publication-33957666455`, ID `9966902322`. It contains `commit-map.json`, `regressions.xml`, `published-head`, the submitted series, and `published.bundle`. Archive SHA256: `f3728d829ba7c8c3df432843196bdbf47dee85002110fd4914aa4ac3e1bf240b`.

## Acceptance still open

- **OP-02:** input overrides are not implemented. A local input-bearing compilation probe also hit `A2P_PINELIB_UNBOUND_PARAMETER`; resolve the compiler/PineLib binding and runtime input delivery together. Do not replace Pine source strings to simulate inputs.
- **OP-03:** the engine still rejects zero margins. Zero is now preserved and rejected explicitly, not silently changed to 100. Declaration/version defaults, per-field provenance, immutable effective configuration and all rounding aliases remain open. Process-local resources that cannot cross the worker boundary are rejected explicitly.
- **OP-04:** CORRECTED/REVOKED envelopes are rejected pending snapshot revision admission, not replayed. Snapshot identity validation is not a full snapshot digest proof. Complete bulk/interactive admission and lifecycle equivalence remains open.
- **OP-06:** sealed result manifests, bounded result chunks and explicit full/metrics-only profiles remain open. Preserved collections can consume more memory than the former reduced output mode; no performance improvement is claimed. A separate valid partial/early-stopped result contract is still needed.
- **OP-05 and OP-07–OP-11:** lifecycle/fill recalc, the full strategy bridge, request integration, metadata and real checkpoints are not closed by this series.
- **OP-36:** branch consolidation remains incomplete. No branches were deleted and main/historical releases were not merged or modified in this pass. The existing `ops/rc6-delivery-branch-selection` branch published the gated series; no new branches were created.

Other review tasks have not been reclassified as complete. No TradingView 1:1 claim, benchmark result, Python 3.11 verification, or production-release acceptance follows from this CI run.

## Exact sibling sources used in CI

| Repository | Commit |
| --- | --- |
| openpine-contracts | `904e8f660834a10d3382cd1b2ed7380c24b73072` |
| pine2ast | `892fee8c2b0443e702918248f2d2642c877723e7` |
| ast2python | `2655b31a826d43b9df5a88c25186a69377eb09e2` |
| pinelib | `66f08b901fabba45ae733cc05cb217066fefa646` |
| backtest_engine | `7532fb34d7b1586f4a49075d29007d00f45527e1` |
| marketdata-provider | `0342759363ebe168f89871a481cb5711a022ee7d` |
| optimizer | `5a62efc672a08e05f7443d3b678fa2595249935a` |

Sibling repositories were not modified. These pins document the tested source set; they do not complete the immutable wheel-release work in OP-01.
