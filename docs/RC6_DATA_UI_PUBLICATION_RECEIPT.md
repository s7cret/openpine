# OP-21 / OP-09 / OP-28 — data and parity UI publication, 2026-09-05

## Exact reviewed code

OpenPine base: `8a2ba66cf7fd5086a913481afdf969c0816849bb`.
OpenPine code head: `5a900fb7728b02a37f03e2ac0cd8004dbb03efd2`.
Provider base: `dcc2ceeb4e917173345227f670dd71e006084d48`.
Provider code head: `4e269bb7e3d389cae6214da9e17898c32c7ead15`.

The series contains five functional code commits, one test-harness correction and four CI/source-pin commits. Documentation receipts after this source head do not change the tested runtime or UI.

| Repository | Commit | Scope |
| --- | --- | --- |
| marketdata-provider | `067096c7a857d15b929f70cd6c257380142038e7` | OP-21 strict offline input, bounded batch parsing, CLI policies |
| marketdata-provider | `4e269bb7e3d389cae6214da9e17898c32c7ead15` | OP-09 positive timeframes, distinct month/minute and provider-to-Pine notation |
| OpenPine | `246360e4680536adf36311489f181df0319e2819` | OP-09 hourly chart and admitted pointvalue in both transports |
| OpenPine | `3add74cb474c5131c59a1736e485ab20f2269bd1` | OP-28 parity page and polling request generations |
| OpenPine | `e9cb9b48416c2bc5f0714aff7e8ac743c0263f7e` | OP-28 visualization loadAll races and bounded display sampling |

The other OpenPine source commits are `af38dfb7060c79211c87d12854bdc0aa45232327`, `c23cde33e3a563bb90ac294e98d7207767584f6b`, `141876ed3c29ac4299e8857b77af5ad8499ecc42`, `50b2a448b100f083124dc40f5065e763f4d4b4cb`, and `5a900fb7728b02a37f03e2ac0cd8004dbb03efd2`. They preserve exact source pins, provider extras, the SSR test context and correct build/API-contract verification order. No tests or source checks were removed to permit publication.

## Implemented and migration behavior

**Offline data.** Missing time never becomes epoch zero. Actual zero timestamps and volumes remain valid. CSV/Parquet share Decimal-first finite OHLCV validation, explicit timestamp units, close-interval validation and optional bound instrument/timeframe identity. Duplicate/out-of-order rows and conflicting aliases are rejected rather than sorted/repaired. Missing volume is rejected by default; `missing_volume='zero'` / `--missing-volume zero` is an explicit opt-in. Query range boundaries always remain UTC milliseconds, regardless of source timestamp units.

CSV is iterated; genuine Parquet uses projected columns and `iter_batches`, default 4096 rows. All rows are validated, even outside the selected range or after max_bars. Only matching output bars are retained. This avoids whole-file intermediate materialization, but still scans the full file and materializes the selected result. Repeated intrabar queries are not indexed yet. Source files are never rewritten. Import mapping/provenance reports, row-group pushdown, immutable snapshot and concurrent-write guarantees remain open parts of OP-21.

**Chart metadata.** Provider `1h`/`4h` becomes Pine `60`/`240`. `1M` stays a calendar month; it is not `1m`. Unsupported multiple-month provider values such as `2M` now fail explicitly rather than becoming two minutes. The worker uses admitted pointvalue rather than a default of one. Compiled hourly Pine checks both fields and produces a real broker order in bulk and interactive execution. This is not complete non-crypto metadata support or proof of all contract/margin calculations; instrument_type/base-currency derivation still needs work.

**Parity UI correctness.** Page requests and original visualization `loadAll()` capture selection identities. Superseded success/error/loading writes are ignored after selection, reload, deletion or unmount. Old poll completions cannot terminate a newer poll loop. Duplicate pending POST submissions are blocked, summary failures can retry, canceled is terminal, and stale CSV previews cannot change the selected report's locked interval. Epoch guards suppress stale UI writes; they do not cancel remote server jobs.

**Display work.** Bounds are computed by a finite-value scan without spreading large arrays into Math.min/max. Each equity curve is reduced only for display using first/min/max/last per horizontal bucket. Original rows are not mutated; strategy metrics, parity verdicts and numeric downloads are not computed from the display sample. The deterministic helper test used 300,000 points and retained 641 at 320 columns, including endpoints and a spike. This is a point-count check, not an FPS or whole-backtest benchmark. Raw response arrays and other UI work still materialize; this is not constant-memory UI processing. Heatmap and markers are explicitly labeled when based on a top-mismatch sample, and offscreen mismatches are no longer clamped into edge buckets.

## Verification

Provider verification/publication: https://github.com/s7cret/marketdata-provider/actions/runs/33989615973
Joint verification/publication: https://github.com/s7cret/openpine/actions/runs/33991141948

| Functional suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 384 | 384 |
| PineLib | 198 | 198 |
| Ast2Python | 356 | 356 |
| Backtest Engine | 586 | 586 |
| MarketData Provider, deterministic non-network selection | 601 | 601 |
| OpenPine native + explicit affected-path regressions | 649 | 649 |
| Total | **2774** | **2774** |

Each successful job has zero failures, errors or skips. Five existing provider live-network tests were explicitly deselected, not claimed tested. The first four library functional suites are complete; OpenPine is a selected suite, not every project test. Pine2AST and the standalone optimizer suites were not fully run. Existing optimizer integration tests did run.

Frontend: **152 Vitest cases plus 22 Node cases**, all passing; vue-tsc and Vite production build passed. The two new hourly metadata scenarios passed with real protected worker processes in both modes. Sandbox protections were retained. Python lint/compile and wheel/sdist builds succeeded on both interpreters. The exported API document contains 89 path entries and the declared WebSocket route. These counts repeat between interpreter jobs and earlier CI attempts and must not be added as unique tests.

Downloaded archive SHA256 values, JUnit cases and Git bundles were verified locally:

| Artifact | SHA256 |
| --- | --- |
| Provider publication / 3.13, 9976222505 | `863d73bc534f8c8c37cfb83ad1dfdd5629dc92c605786e5605f1b40f18591c7a` |
| Provider 3.11, 9976217421 | `10bca61f32bcf4974a070bc7297a97f1e3180700d45f9360f05811f487e466b0` |
| Joint 3.13, 9976707201 | `6e5fc54e39e6ace8ade57eb3354e3f51975623e385d091e216a3a9a0ffc995fd` |
| Joint 3.11, 9976707908 | `00a4c53e639a0c92f2abf56eb70d85cc75fef65f5eea4d6e4472c16a0c9f1a9d` |
| Frontend, 9976714991 | `e75862c849f0638ac4c8024401094d3ee4dce553fad1444a14a6f869a7ea01b5` |
| Final publication receipt, 9976747920 | `9f3fff7d9696785dc7ad37cae9589e9811aa573b2c8dbe738e3a41e277139f7d` |


The UI tests include 17 actual Vue setup/watch/unmount scenarios driven by delayed promises and in-memory renderers, not mocked replacements for the page logic. DOM layout, browser interaction, accessibility and canvas pixels were not verified. The 22 Node tests include production-package/static/proxy behavior and method/path checks against OpenAPI exported by the same tested backend. TypeScript and Vite compilation are actual production builds, not fabricated dist files.

The initial CI attempts correctly blocked publication. First, new custom renderer tests needed Vue SSR module registration in the Node test environment. Then existing packaging/API contract tests exposed missing prerequisites: real dist and a current backend schema. The final setup supplies both, keeps all assertions and consolidates permanent native/UI checks into one read-only workflow to avoid duplicate backend runs. The original main/release quality gates are not replaced. Dependency vulnerability audits and live exchange availability are separate acceptance work.

## Publication and preservation

Both source branches contain the exact original reviewed commits. The provider published after its two interpreter jobs. In OpenPine, all three verification jobs first passed, but the Actions publication token could not update workflow files. Its initial release push was rejected for missing workflows permission, not a test failure.

The already verified commit and tree were read back from GitHub, the release head was rechecked, and the authorized connected GitHub API advanced RC6 with force=false. No permission settings were changed. Retrying the failed publication job verified the existing expected release head, archived the maintenance tip and removed its branch. That retry completed successfully. The verification jobs were not weakened or relabeled to hide the initial delivery failure.

Archive tags `ops/rc6-data-ui-20260905` retain maintenance tips `64ea47fcfc078b3b33fa09720208885f0bc3bd4c` in the provider and `cef5365763bf6aae94b896e012645901c4c8a22d` in OpenPine. Temporary helpers/readable patch submissions are not in the RC6 source tree. Main and historical release heads were untouched. The downloaded final receipt and a fresh remote read confirm exactly four OpenPine branches:

```
main                     af697b28b12b672ab46442ef9a3e9f6d241802d4
release/v2.17             3c026a875c6b83609e4a3fb1183a89a22afd01ef
release/v4.0.2            a0b89269bed8d571faf65909e2ac4dbd091764e2
release/5.0.0rc6          5a900fb7728b02a37f03e2ac0cd8004dbb03efd2
```

A documentation-only receipt may subsequently advance RC6; this table records the verified source publication, not a claim that documentation has the same SHA. No unrelated provider branches were deleted.


## Remaining compiler / release gaps

Fresh real compilation probes still fail for request.security and request.security_lower_tf with `A2P_PINELIB_INJECTION: unsupported PineLib injected parameter source`. The compiler/PineLib request-expression lowering and child context must be implemented before claiming provider integration. `na(strategy.position_avg_price)` still fails with `S4_CALL_ARGUMENT_TYPE_EVIDENCE`. These failures are recorded separately, not counted among the accepted passing cases.

The exact sibling set is `RC6_LIFECYCLE_SOURCES.json`. Update the source set together: matching package version strings do not establish matching code. Immutable production installation, requests, complete strategy exits/risk/indexed trades, genuine checkpoints/state identity, full realtime, browser UX and Pine v1-v6 TradingView oracle coverage remain open. No final production release or completion of all 36 tasks is claimed.
