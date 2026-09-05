# Request/checkpoint continuation and numeric boundary verification

Date: 2026-09-06 (Europe/Vilnius); verification finished 2026-09-05 UTC.

## Published code

Code head: `0ab826a7a71a6010f0eed680405cdc36b68bc88a`.
Code tree: `f26fb42540ab77912e53f85db95bc10ea0214a26`.
Parent: `efa90c9723391847a6a96d66901532820875e2dd`.

The newer request/checkpoint/capability series was already published when its release ref was re-read. It was preserved, not replaced by the older reconstructed request-only candidate. Unattached recovery commits are not additional delivered changes. The actual new code commit fixes the common canonical numeric admission boundary and adds 31 parameterized regression cases. Its only changed files are `openpine/runtime/rc6_marketdata.py` and `rc6_tests/test_rc6_numeric_boundary.py`.

A finite, nonzero decimal such as `1e-999` previously converted silently to float zero. Each field is now converted once and rejected if the nonzero value underflows to zero. Genuine zero and representable subnormal values remain accepted. Decimal OHLC invariants and volume checks are retained. Both worker decoders and preloaded request snapshots use the same boundary; rejected input does not advance the stream cursor.

The six preserved request-series source commits, supported semantics and restrictions remain documented in [RC6_REQUESTS_CHECKPOINT_PUBLICATION.md](RC6_REQUESTS_CHECKPOINT_PUBLICATION.md). Requests evaluate supported expressions on independent source contexts. Generated checkpoints contain actual Pine/request state and causal receipts, but are not complete broker/IPC/process restart tokens. No stronger capability or full TradingView parity claim follows from these tests.

## Exact final code CI

Run: https://github.com/s7cret/openpine/actions/runs/33999714708

| Suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 384 | 384 |
| PineLib | 221 | 221 |
| Pine2AST | 338 | 338 |
| Ast2Python | 370 | 370 |
| Backtest Engine | 586 | 586 |
| MarketData Provider, non-network | 601 | 601 |
| OpenPine native and explicit affected paths | 777 | 777 |
| Total | **3277** | **3277** |

Both JUnit sets contain zero failures, errors and skipped cases. The same 3,277 cases run on two interpreters; previous 3,246-case results and local reruns overlap and must not be added as unique tests. The first five library suites are complete functional inventories. Five existing provider live-network cases are excluded; full OpenPine and standalone optimizer inventories and coverage thresholds are not certified.

Four request cases run actual protected workers (security/TA and lower-TF arrays, each in interactive and bulk modes). Bubblewrap/AppArmor protections remain in place. Lint, Python compilation, OpenPine wheel/sdist, frontend TypeScript/Vite production build, **152 Vitest cases and 22 Node cases** all passed. Node API checks consumed OpenAPI exported by the same backend. Browser visuals and an external TradingView oracle were not tested.

Downloaded JUnit XML was parsed, code-head receipts and exact source pins checked, and archive SHA256 digests matched:

| Artifact | SHA256 |
| --- | --- |
| Python 3.13, `9979173751` | `5fd759b24f2080e5783c6c961708f798b05a8a024da480582ff85e975461fdf9` |
| Python 3.11, `9979174630` | `34f145e15076a3ec1db44946861a5645a64bb57c307f7328571af7105cdc70b8` |
| Frontend, `9979180474` | `35f19b88224522c0f3151ce1d8d26ef4666f1bfd1023f6cc96acb093a8ce6081` |

The receipt itself changes documentation only. A later documentation-triggered workflow must not be assumed complete merely because this code run passed.

## Explicit unresolved diagnostics

A separate local standalone-optimizer probe failed `tests/integration/test_optimize.py::test_optimize_grid_json`: no recommended trial was returned. Its trial log reports `could not freeze runner root` following the containment deadline for a stable descendant snapshot. This was outside the accepted CI selection. The environment-versus-implementation root cause was not established or fixed here; no process protection was disabled. Existing OpenPine optimizer integration cases did pass the selected CI run.

The successful frontend job's `npm ci` reported **two dependency vulnerabilities (one low, one high)** and a vue-i18n maintenance warning. Detailed advisories and runtime exposure were not investigated in this pass. A successful functional build is not a clean security audit.

## Remaining acceptance and branch state

Automatic request-series discovery/downloading, larger snapshot transport, nested/UDF and live request expressions, complete exits/trailing/risk/indexed trade access, full isolated-job recovery, immutable deployment, cancellation/backpressure, the standalone optimizer gate, dependency audit and browser/oracle acceptance remain open. No whole-backtest speedup or production release is claimed.

Install the compatible set in [RC6_LIFECYCLE_SOURCES.json](RC6_LIFECYCLE_SOURCES.json) together and recompile against the new target manifest. Same package version strings do not prove identical code. The fresh branch inventory contains exactly `main`, `release/v2.17`, `release/v4.0.2`, `release/5.0.0rc6`. Main and historical releases were unchanged; no new branch was created for the numeric fix.
