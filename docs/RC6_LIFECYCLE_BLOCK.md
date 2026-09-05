# RC6 lifecycle integration and branch consolidation — 2026-09-05

## Verified runtime publication

Integrated code head: `295a6885f1094676ae1bfdc90631814daa9e8966`.
Base: `beddc87d852974a9814718f83f2dbc7059f8e3a8`.

| Task | OpenPine commit | Implemented scope |
| --- | --- | --- |
| OP-03 | `0df42766bf55b1bde0c600cc0d7325a19e2ab386` | Preserve supported zero margins and all five rounding modes across worker transport. |
| OP-05 | `b3a4560cd087a12283b0f220e16f185f274fc420` | Common causal callback cursor, accurate dataset bounds, fill recalculation, deferred single history commit, protocol identity checks. |
| OP-06 | `54d8dad9ae6d79f762599fc5de9031c28ce3fbd5` | Reject failed interactive broker results, nonzero/unverifiable exits and incomplete finalization. |
| OP-05 tests | `295a6885f1094676ae1bfdc90631814daa9e8966` | Distinguish repeated entry intents after on-close fill recalculation from actual fills; assert one unit trade and exact causal callback coordinates. |

The last change corrected a test expectation, not runtime behavior. With fill recalculation enabled, a bar-zero entry condition can execute twice. Pyramiding still prevents a second fill. The initial CI correctly blocked publication until the assertion checked both the command tape and real fills.

The exact sibling sources are in `RC6_LIFECYCLE_SOURCES.json`. Contracts `d1382d1` binds a recalculation receipt to the following intent batch, never an earlier batch. Engine `48520cec` includes causal callbacks and reduce-only close protection on top of the newer margin/rounding work. These sibling publications were verified before this integrated run.

## Integrated CI evidence

Run: https://github.com/s7cret/openpine/actions/runs/33977499788

| Functional suite | Python 3.11 | Python 3.13 |
| --- | ---: | ---: |
| OpenPine Contracts | 384 | 384 |
| PineLib | 198 | 198 |
| Ast2Python | 356 | 356 |
| Backtest Engine | 513 | 513 |
| Native RC6 and selected OpenPine regressions | 371 | 371 |
| Total | 1822 | 1822 |

Each matrix job has zero failures, errors and skipped tests. The two jobs execute the same 1,822 cases: 3,644 executions, not 3,644 unique tests. Both build wheel/sdist successfully. Verification has read-only credentials; the separate publication job advanced RC6 only after both passed and the target still matched its expected SHA.

Four new real-process sandbox cases cover interactive/bulk with on-close fills on/off; the existing sandbox, input, optimizer and HTTP tests also run. Bubblewrap/AppArmor protections remain enabled. Direct native tests compare complete intent tapes, final equity, closed trades, historical series length and last-bar flags across transports. Session-level realtime tests distinguish var rollback from varip persistence; this is not full live/realtime parity acceptance.

Downloaded artifacts were SHA256-verified and their Git bundles match the local reviewed history:

- Python 3.13 artifact `9972780002`, SHA256 `7695a3ef9e7b99501b6616cc6e0656a9dadc30722b56ac849a7c1d227beaaa98`.
- Python 3.11 artifact `9972779224`, SHA256 `9e7eec09e26dc0ef3d0c4b5b1f49d54eaedafa4364a6be602a0d9aec3d4369c2`.

## OP-36: reviewed branch retirement

**Completed:** [RC6_BRANCH_CONSOLIDATION_RECEIPT.md](RC6_BRANCH_CONSOLIDATION_RECEIPT.md) records the successful cleanup run, final four-branch inventory and seven retained archive tags.

The machine-readable source-to-preservation mapping is `RC6_BRANCH_SELECTION.json`. Ten functional commits from `fix/data-delete-semantic-profile` already have separate preservation commits in RC6. Deployment identity and the migration fix also have explicit preservation commits. Four RC5 dependency-only commits are deliberately not applied: their changes are limited to the RC5 candidate, old stack-lock and its version assertion. Their complete history is retained by the archive tag.

Target branches:

```
main
release/v2.17
release/v4.0.2
release/5.0.0rc6
```

Retired branches are archived as lightweight tags **with their original names** before their branch refs disappear in the same atomic transaction. This preserves commit history and old revision references, including `release/5.0.0rc5` and `release/5.0.0rc6-local-candidate`. Main and the two historical releases are not merged or rewritten. The current RC6 is not promoted to main.

The maintenance publisher and verified source-archive workflow are retained in RC6. `scripts/consolidate_reviewed_branches.py` checks the exact 11-branch inventory, source preservation, successful integrated CI and expected branch/tag identities. Its atomic push uses explicit compare-and-delete leases: a concurrent commit aborts the whole transaction. Nine local bare-repository tests cover archival success, inventory/tag drift, unreviewed code and a racing commit. The same nine tests passed in the separate consolidation Actions run before cleanup; the downloaded receipt verifies its final refs and archive bundle.

Restore a retired branch deliberately from its archive tag, for example:

```sh
git fetch origin --tags
git switch -c release/5.0.0rc5 refs/tags/release/5.0.0rc5
```

## Acceptance still open

This is not a full test run of all seven libraries or all OpenPine tests, not a TradingView oracle comparison, and not performance/coverage/release acceptance. No measured speedup is claimed. OP-01 immutable deployment, complete request integration, full strategy namespace, real checkpoint/state-hash parity, comprehensive realtime and UI parity remain open. The zero-margin transport gap is closed for the tested engine, but all declaration/version defaults and immutable configuration provenance are not. Result sealing/chunking and the full set of 36 review tasks are not complete.

Install the compatible source set together; do not mix this OpenPine with stale same-version wheels. The CI wheel is an OpenPine distribution, not a bundled production release of the whole stack.
