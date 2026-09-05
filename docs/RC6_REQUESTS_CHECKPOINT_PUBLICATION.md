# OP-08 / OP-10 / OP-14 / OP-15 — verified request and checkpoint integration

Date: 2026-09-06 (Europe/Vilnius; CI completed on 2026-09-05 UTC).

## Publication is complete for this scoped implementation

OpenPine runtime head: `53f0c2d6da67bf364e0962fb50a7107571182427`.
Runtime tree: `5cc100e2c1a8a4aa724e57f4a22d3d92c2620080`.
Base: `60c615cab534ce948aebc15a1a32681823bacee7`.
Joint verification and publication: https://github.com/s7cret/openpine/actions/runs/33998764352

The previous unconfirmed-publication status is superseded: the original request/checkpoint commits and three additional hardening commits are in RC6. The downloaded Git bundle verifies the complete history and the exact published head. This receipt and the permanent CI extension do not change runtime or UI code. This is not acceptance of all 36 tasks or complete TradingView parity.

| OpenPine commit | Task and implemented change |
| --- | --- |
| `1a0bbbf5066102573989cf72c966ea459ca641cf` | OP-08: admitted canonical request snapshots in both isolated transports |
| `1d65d3f0413efb9579df39a51df8f8780bc01168` | OP-10: real generated-session checkpoint and atomic restore |
| `104c1730763bfbc84dcfb8b3d1f60cca87ef7562` | OP-08: exact published compiler/runtime source pins |
| `d106015f22f838dbd8789a42185883ab20741a78` | OP-10: callback receipts cross-check counters, times and causal order |
| `63a24ea01f0597d5452983a7cb299c8c4dad3416` | OP-08: static request preflight and future-data causality regressions |
| `53f0c2d6da67bf364e0962fb50a7107571182427` | OP-15: advertise and negotiate only implemented worker protocol capabilities |

## Required published sibling revisions

The complete seven-library set remains in `RC6_LIFECYCLE_SOURCES.json`. The request-aware changes are:

| Repository | RC6 source head |
| --- | --- |
| Pine2AST | `6ba935eceee505bcbe80bf1f5908588407d2f546` |
| PineLib | `10a03a46ffb5f94143c473139e8a2bcf0e593d3b` |
| Ast2Python | `0e956411d7dff8b1a68fba3b5af6e63d371c6781` |

Fresh GitHub reads confirmed these release refs. Update the source set together and recompile generated modules against the new target manifest. Matching package version strings do not identify identical code. Immutable production wheel/installation acceptance remains OP-01, not solved by this receipt.

## Requests: evaluated on the source context, not chart substitutes

The compiler emits dependency-sliced methods from checked IR. `request.security` evaluates supported scalar or tuple expressions in independent source runtimes. History such as `close[1]`, supported TA, immutable global dependencies and applied inputs use the requested bars and metadata. The expression is not a Pine string evaluated by substitution and is not a precomputed chart value.

`request.security_lower_tf` returns independent Pine array handles, including tuples of arrays. Old handles retain their values after later callbacks and after checkpoint restore. Tests cover missing intrabars, invalid higher timeframes with the ignore flag, named-argument reordering, gaps/carry-forward, historical lookahead choices and source pointvalue. The former `na(strategy.position_avg_price)` type/ABI barrier is fixed; an actual compiled flat-position condition now creates an order. V6 boolean NA restrictions are retained.

Source rows are canonical, immutable preloads. Data and metadata are bound to the execution context, effective config and provider identity. Invalid or missing manifests fail before worker staging. The additional preflight checks literal source identities even when the overall manifest is valid; unresolved expressions remain runtime decisions rather than guessed mappings. Future-only changes to requested rows are tested not to change earlier no-lookahead decisions.

This is an explicit preload API, not automatic request discovery or network downloading from the UI. A caller constructs `config.request_manifest = build_request_manifest(execution_context, datasets)`, where each dataset supplies instrument_id, provider timeframe, market, all InstrumentContext fields and original FINAL provider envelopes. Use the explicit metadata tickerid in Pine. Empty Pine symbol/timeframe inherits the chart context, which must also be preloaded. No network or new filesystem permissions are granted to worker code.

Limits: at most 64 datasets, 250,000 bars per source, and a 4 MiB serialized manifest. The byte limit normally binds first; these are implementation bounds, not TradingView subscription limits. Source lists and results still materialize. The caller owns the chosen snapshot horizon and correct preload coverage. Nested requests, UDF capture, unsupported local/mutable dependencies, broker/barstate expressions, live revisions, ticker/session transformations and currency conversion remain unsupported or restricted.

## Checkpoints: real state plus cross-checked output cursors

The bulk placeholder is gone. Generated-session export contains Pine series, slots, references, request cache/child state, transcript and callback/intent cursors, bound to the artifact, inputs, source set, run and config. PineLib now decodes portable NA markers at the segment boundary exactly once.

The additional v2 envelope stores an append-only callback receipt journal. Restore derives the intent count from receipts and replays causal callback ordering against the runtime transcript and committed bar timestamps. Rehashed outer counters, mismatching flags/times, missing/reordered receipts and provisional bars are rejected. A separate candidate runtime is validated before replacement, so failed restore leaves the current session untouched. Tests compare uninterrupted execution with JSON-serialized restore, exact subsequent intent tapes, state/semantic hashes and continuation after a fill-recalculation boundary.

Receipt checks establish internal consistency, not authenticity against an attacker able to replace all data and all checksums. They are not signatures, trusted external storage or TradingView correctness proofs. The earlier v1 generated envelope without receipts is intentionally not accepted silently.

`export_resume_state=True` retains real strategy state in bulk output, but full broker/IPC/process recovery is NOT implemented. The worker now advertises only `closed_bar`, validates HELLO/INIT_RUN negotiation and rejects `checkpoint_v1` before bar execution. A generated-session checkpoint must not be sold or used as a complete isolated-job restart token. OP-10 and the full OP-15 capability graph remain partial.

## Verified test scope

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts, complete functional suite | 384 | 384 |
| PineLib, complete functional suite | 221 | 221 |
| Pine2AST, complete functional suite | 338 | 338 |
| Ast2Python, complete functional suite | 370 | 370 |
| Backtest Engine, complete functional suite | 586 | 586 |
| MarketData Provider, deterministic non-network selection | 601 | 601 |
| OpenPine native plus explicit affected-path selection | 746 | 746 |
| Total | **3246** | **3246** |

Both JUnit sets contain zero failures, errors and skips. These are the same 3,246 cases on two interpreters, not 6,492 unique tests. Five existing provider live-network cases were excluded deliberately. Full OpenPine inventory, standalone optimizer, coverage thresholds and external TradingView oracle acceptance are not claimed.

Four new real-worker cases cover security/TA and lower-TF arrays in interactive and bulk modes. They passed without disabling Bubblewrap/AppArmor. Frontend evidence contains 152 passing Vitest cases and 22 passing Node cases, with actual TypeScript/Vite production build and API checks against the same backend's exported OpenAPI. OpenPine wheel/sdist builds and changed-source lint also passed. Browser visual/E2E acceptance is separate.

The permanent read-only `rc6-native.yml` is extended to retain the full Pine2AST suite, all request/checkpoint/capability lint paths and the exact code-head receipt. Its subsequent run is distinct from publication run 33998764352; a new result is not assumed by this document.

## Independently checked publication artifacts

Downloaded archives were checked against the GitHub artifact SHA256 digests; JUnit XML was parsed, source pins compared, and both Git bundles verified. The publication receipt records a normal RC6 fast-forward and maintenance archival. Final refs show main and historical releases unchanged and exactly four OpenPine branches. Tag `ops/rc6-requests-finalize-20260906` preserves maintenance commit `d380be69dcbb7b330d4d275c80dad2c43945aa08`. Runtime source contains no new one-shot publication tools.

| Artifact | SHA256 |
| --- | --- |
| Publication/3.13 `9978914398` | `765118e7a7e37cb0f667ee39ad45bc527242bb6e4cec681e2472b6d604acc117` |
| Python 3.11 `9978900894` | `a44297fb793e390bd000a20f6e34f41de96245fbcdae164091a4702578a2fcea` |
| Frontend `9978911435` | `ee2d8a3c03b21d767baf4eff43674da6b7657d066017236698a09607486de9d4` |

## Still open

OP-08 automatic data discovery/large snapshot transport, nested/UDF and live requests; OP-10 full isolated-job resume; OP-14 full version-specific signatures; OP-15 complete end-to-end capability graph; full strategy exits/trailing/risk/indexed trades; immutable delivery; cancellation/backpressure; browser UX; full TradingView oracle coverage. No whole-backtest performance measurement was made. Binary-search alignment and request-cache reuse are implementation improvements, not a measured speedup claim.

Semantic references, not execution-oracle evidence:
- https://www.tradingview.com/pine-script-docs/concepts/other-timeframes-and-data/
- https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/
- https://www.tradingview.com/pine-script-docs/v4/essential/context-switching-the-security-function/
