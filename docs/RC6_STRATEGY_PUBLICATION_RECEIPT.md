# Strategy block publication receipt — 2026-09-05

## Published original commits

The local candidate has been published without reimplementation, squash, source changes or changed source commit identities.

| Repository | Commit | Scope |
| --- | --- | --- |
| `s7cret/backtest_engine` | `5898f30f976a2bb17df4b3dbd8c738de8a4e83ac` | OP-20: cancel bracket children by the public exit ID |
| `s7cret/backtest_engine` | `70dd7ccfecaabafa3000d359770305acb32c8b9e` | OP-07: shared seven-command registry and 17 scalar state values |
| `s7cret/openpine` | `325b2be8b0d320a1c30651094545ef5d50bbc249` | OP-07: connect both transports and admit supported host capabilities |

Both target branches are `release/5.0.0rc6`. Engine base was `48520cec7e2dafc5755a2e003b1ebf9f48b2163a`; OpenPine base was `0b8b48e2463f7a66330276594a008f2bee37a99d`. The OpenPine code tree is `158479d21752382aadfa496cdf7c57f924a3353c`; the engine code tree is `79754639a23f19d24fdc7a27d4c4621798932cd6`.

This receipt and the accompanying status corrections are documentation only, after the verified source commit. The original commit message describes the earlier blocked attempt; that historical wording is superseded here, not rewritten.

## Independent verification before release-ref updates

Engine CI: https://github.com/s7cret/backtest_engine/actions/runs/33987273940
Joint CI: https://github.com/s7cret/openpine/actions/runs/33987487240

| Joint functional suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 384 | 384 |
| PineLib | 198 | 198 |
| Ast2Python | 356 | 356 |
| Backtest Engine | 586 | 586 |
| Native RC6 and selected OpenPine regressions | 639 | 639 |
| Total | **2163** | **2163** |

Both jobs have zero failures, errors and skipped cases. The same 2,163 cases execute on two interpreters: 4,326 executions, not 4,326 unique tests. The separate engine CI repeats the 586-case broker suite and must not be added to the unique total.

The seven real-process tests previously omitted from local execution are included. The two new `test_real_worker_strategy_surface` variants (interactive/bulk_backtest) passed on both interpreters with actual Bubblewrap/AppArmor protection. All existing native and explicitly selected optimizer/HTTP cases also ran. No test was weakened, skipped or modified to make publication pass. Changed-file Ruff checks, compileall and wheel/sdist builds succeeded for both repositories. The prior local diagnostic process crash was not reproduced by this clean joint run; its root cause was not investigated here.

Four library functional suites are complete; OpenPine uses the native plus explicit affected-path selection. This is not the complete test inventory of all seven libraries, a coverage gate, a TradingView execution comparison or production release acceptance. OP-07 remains partial as documented in `RC6_STRATEGY_HOST_BLOCK.md`.

## Exact publication and preserved branches

Readable patches were checked by SHA256. Each restored tree and raw Git commit was checked against its original SHA before any test or release-ref update. Verification jobs used read-only repository permissions. Separate publication jobs rechecked the expected remote base and used ordinary fast-forward pushes, never a forced release update.

Temporary `ops/rc6-strategy-publish-20260905` branches were archived as same-name tags and removed after successful publication:

| Repository | Preserved maintenance tag SHA |
| --- | --- |
| Backtest Engine | `2fae7fce641152135c745e7fd4e0ba54a60222ee` |
| OpenPine | `3a4c8567a3cf49a609e45c87d13dc10ce98e7661` |

The temporary tag/archive transaction used exact expected-ref leases. Original `main` and historical releases were untouched. OpenPine again has exactly four branches: `main`, `release/v2.17`, `release/v4.0.2`, `release/5.0.0rc6`. No cleanup of other preexisting engine branches is claimed. Publication helpers and patch submissions remain only in archived maintenance history, not the RC6 runtime tree.

Downloaded archives were SHA256-checked, their JUnit XML parsed, and Git bundles verified. Remote release refs were read again and match the original local candidate commits.

| Artifact | SHA256 |
| --- | --- |
| Engine publication `9975549371` | `53532a91692c4ff95d510fb18be3495da36098c320696b44f4d5987885b73baa` |
| Joint publication / Python 3.13 `9975650489` | `c01933816314f99c2375685f3d19ef5ad35aa8a2ddb5fa9628ec05bad9083746` |
| Joint Python 3.11 `9975645810` | `8ad501b5571bb1d3797a36df82ce4b50e14fc87225b73233f4587dda2bc2ea07` |

The source set is `RC6_LIFECYCLE_SOURCES.json`. Update OpenPine and its pinned engine together; identical package version strings do not ensure identical code. The CI wheel is not a self-contained production distribution of the full stack. Immutable deployment, requests, complete exits/risk/indexed trade access, checkpoints, broader realtime and browser UX remain separate work.
