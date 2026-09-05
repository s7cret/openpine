# RC6 inputs and isolated optimizer block — 2026-09-05

## Published source changes

| Repository / task | Commit | Scope |
| --- | --- | --- |
| PineLib / OP-02 | `c6eb153b7ed57d7c20293c20077a1b63a2d439da` | Freeze InputRegistry, detach options, normalize float/price, validate defaults independently of overrides, expose read-only applied values and their hashes. |
| OpenPine / OP-02 | `d1bb4fec653fc29da44425e45f1dbbd310df744b` | Resolve existing sealed SCRIPT_METADATA into actual PineLib inputs; deliver values through both worker transports and bind them to durable run configuration identity. |
| OpenPine / OP-26 | `a175c47ac2f95b0949344a82300f2e7435aa7b21` | Independent trial config, required outputs/metrics and warmup forwarding, input/result evidence, explicit unsupported-request rejection and data-drift checks. |

The two source repositories use `release/5.0.0rc6`. The service commit `59a4465ec3567674604ea5fb38498733cf3a6fe4` is on the existing `ops/rc6-delivery-branch-selection` branch. No new branches, branch deletions, protected-branch changes or historical-release changes were made.

Useful existing RC6 sibling work was preserved: Ast2Python already had typed and legacy input descriptors and the input ABI, while PineLib also had input admission and semantic-state fixes. This block integrates that work; it does not claim those existing commits as newly implemented changes.

## Input behavior and identity

The preflight reader parses only literal SCRIPT_METADATA without executing generated code. The descriptor lives inside the module covered by emitted_module_hash. Stable IDs are scoped to that compiled artifact, not promised across arbitrary source edits. IDs, unique variable aliases and display titles are distinct; duplicate titles do not collide.

Input type, bounds, options and unknown or ambiguous overrides are rejected before worker startup. Permitted false, zero and empty string values are preserved. Explicit empty API overrides reset defaults rather than restoring previously saved strategy params. A new execution installs a new registry; registry values and internal fields cannot be changed through normal assignment after admission.

Bootstrap carries canonical values and their expected hash. The worker resolves and checks them; bulk result admission verifies input_values_hash, input_registry_hash and effective_inputs against the parent. CLI, backtest API, strategy jobs, TV-parity and optimizer admission pass the selected inputs into run configuration hashing. This wiring is not a full UI/CLI/API end-to-end digest certification.

## Concrete regression evidence

For close values 1 through 10, SMA length 2 produces `[na, 1.5, 2.5, ..., 9.5]`; the same compiled source with length override 7 produces six initial na values followed by `[4, 5, 6, 7]`. Both are checked through the real frontend, emitter and PineLib for Pine v5/v6. Legacy generic input() override scenarios are checked for v1-v6; this does not establish complete language compatibility.

A sensitive optimizer strategy with quantities 2, 7, then 2 produces final_equity 100002, 100007, then 100002 from initial capital 100000. The repeated trial reproduces metrics and applied-config hash; the different input changes them. Only process transport is replaced in this particular focused test; compilation, PineLib and broker execution remain real.

Seven separate CI cases use actual isolation: the baseline worker scenario, quantity 2/7 in interactive and bulk modes, an external optimizer run, and an optimizer HTTP route. Sandbox protections were not disabled.

## CI and publication integrity

Run: https://github.com/s7cret/openpine/actions/runs/33962141839

| Python 3.13 suite | Passed | Failed / errors / skipped |
| --- | ---: | --- |
| Complete PineLib | 189 | 0 / 0 / 0 |
| Complete Ast2Python | 346 | 0 / 0 / 0 |
| Native RC6 plus selected OpenPine regressions | 340 | 0 / 0 / 0 |

Local verification of the selected OpenPine suite: 333 passed; the remaining seven actual sandbox cases passed in CI. Do not add local and CI counts. This is not the complete test suite of all seven libraries.

Each published file was reconstructed with Git blob SHA verification, tested before publication, and published by fast-forward only. Downloaded publication code matches locally tested code; the pre-existing remote progress document was preserved. Tested OpenPine code tree: `b94a1fea0ea6c19a3b7a2502bc2230e32de72fd6`.

Artifact: `reviewed-rc6-publication-33962141839`, ID `9968288681`. Downloaded archive SHA256: `86616a34e4cf6714f7101e58af04b3f67e11bd18ed2bc804d0b92227cfd33a48`. It contains commit-map.json, the three XML reports, published-head, the submitted series and a Git bundle.

## Exact tested sibling sources

| Repository | Commit |
| --- | --- |
| openpine-contracts | `904e8f660834a10d3382cd1b2ed7380c24b73072` |
| pine2ast | `892fee8c2b0443e702918248f2d2642c877723e7` |
| ast2python | `ad1ece63c05270051138523eab290f7ca6cd396e` |
| pinelib | `c6eb153b7ed57d7c20293c20077a1b63a2d439da` |
| backtest_engine | `7532fb34d7b1586f4a49075d29007d00f45527e1` |
| marketdata-provider | `0342759363ebe168f89871a481cb5711a022ee7d` |
| optimizer | `5a62efc672a08e05f7443d3b678fa2595249935a` |

Updating OpenPine alone is insufficient: this block requires the listed coherent PineLib/Ast2Python revisions. Identical package version numbers are not proof of identical sources. These pins document tests, not a completed immutable wheel/deployment lock under OP-01. Other sibling repositories were not modified in this block.

## Acceptance still open

- OP-02: full input UI/capability surface, dynamic input settings and external sources, plus identical output digest through the complete CLI/API/optimizer path. The tested generic-input cases do not establish all Pine v1-v6 semantics.
- OP-03: the new hash binds transported settings to normalized applied inputs. One immutable effective configuration after all broker-side normalizations, field provenance and zero-margin engine semantics remain separate work.
- OP-26: full canonical production fingerprint contract, serial/parallel/reordered acceptance and full warmup/score protocol. Range, seed and early_stop_conditions are explicitly rejected by this isolated runner when requested, not newly implemented. No speedup or benchmark claim is made.
- OP-36: final consolidation to four branches remains open; OpenPine still has 11 branches.

No TradingView execution comparison, UI E2E, complete seven-library pytest run, or Python 3.11 verification of these new changes was performed. Other lifecycle/recalc, strategy bridge, request integration, metadata and checkpoint gaps remain open.
