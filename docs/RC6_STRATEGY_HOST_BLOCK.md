# OP-07 / OP-20 — native strategy host (2026-09-05)

## Status

**Published and jointly verified.** The local code commit `325b2be8b0d320a1c30651094545ef5d50bbc249` is now in OpenPine RC6, and engine commits `5898f30f976a2bb17df4b3dbd8c738de8a4e83ac` and `70dd7ccfecaabafa3000d359770305acb32c8b9e` are in Backtest Engine RC6. Original source SHAs and code were preserved. Joint CI passed on Python 3.11/3.13 with real protected workers. See [RC6_STRATEGY_PUBLICATION_RECEIPT.md](RC6_STRATEGY_PUBLICATION_RECEIPT.md) for results and branch receipts.

The earlier publication-blocked wording in the original commit message is historical; it is not the current status. The source pin file now references a published engine commit. No main or historical release was changed.

## Implemented

- One executable engine-owned registry binds `strategy.entry`, `order`, `close`, `close_all`, `cancel`, `cancel_all` and a restricted `exit` form. Parameter binding and dispatch share this registry, not duplicated support lists.
- The host exposes 17 phase-coherent scalar strategy values and the 14 existing constants. Equity, profit/loss, trade aggregates, position, initial capital and account currency come from the current broker callback. Interactive aggregates use cumulative win/loss/even counts, never the frame's incremental closed-trade list.
- OpenPine checks the generated module's literal host calls at compilation, again after artifact identity verification before worker staging/spawn, and in the worker before executing the module. Unknown capabilities, unsupported exit shapes and wrong canonical bindings fail explicitly. Compile metadata records the required capabilities and host-surface hash. The surface hash is not an ABI signature or TradingView parity proof.
- Historical named `when` is applied; false/no-op calls do not consume intent sequence numbers. Numeric historical values retain Pine boolean conversion. The worker language profile now follows the admitted Pine version rather than always saying `pine-v6`.
- Engine cancellation matches both the public order ID and `parent_exit_id`. Cancelling a bracket's public ID cancels its active/pending TP and SL children while leaving unrelated orders intact.

## Deliberate limits — OP-07 remains partial

`strategy.exit` requires an explicit nonempty `from_entry` and an already-open matching entry in the broker snapshot. It supports ordinary absolute/tick price legs and partial quantities. Pending-entry exits, unqualified/all-entry exits, trailing parameters, per-leg metadata and v6 relative+absolute price pairs are not admitted. There is no silent fallback to pre-v6 mixed-price behavior.

Risk commands, indexed trade methods such as `strategy.closedtrades.profit(i)`, and unsupported state values are not advertised as implemented. Some calls are rejected by the upstream compiler before reaching the host gate; indexed result calls currently produce `A2P_DELEGATED_RESULT_REQUIRES_COMMIT`. Diagnostics for emitted state reads can reference a generated line when no original source span exists.

The old optional positional layouts are not guessed: v1-v5 entry/order accept only their shared leading eight positions, exit its leading twelve, close and cancel their initial ID, and close_all/cancel_all require named optional arguments. This is a conservative compatibility restriction, not complete historical signature coverage. The frontend's version catalog still needs separate audit.

Alert message/disable metadata is retained in intent records; external alert delivery is not implemented by this block. Risk semantics, complete command availability by version, margin/liquidation getters and the whole TradingView namespace are not certified.

A separate existing compiler probe `na(strategy.position_avg_price)` fails with `S4_CALL_ARGUMENT_TYPE_EVIDENCE`; this block does not fix that lowering/type-evidence path. The scalar snapshot test verifies flat-position `na`, while actual compiled Pine tests use the average price after entry. No claim is made that all combinations of scalar expressions compile.

## Historical local verification before publication

The following is the original pre-publication record. The later joint CI supersedes its publication and process-verification status, not the documented functionality limits. Python **3.13.5**, exact sibling sources recorded in `RC6_LIFECYCLE_SOURCES.json`:

| Suite | Passed |
| --- | ---: |
| OpenPine Contracts, complete functional suite | 384 |
| PineLib, complete functional suite | 198 |
| Ast2Python, complete functional suite | 356 |
| Backtest Engine, complete functional suite | 586 |
| OpenPine native RC6, two disjoint groups excluding real-process cases | 171 + 87 = 258 |
| Total non-overlapping local cases | **1782** |

JUnit reports have zero failures/errors/skips for the completed selected runs. **Seven native real-process cases were deselected locally**, including two added here, because the required Bubblewrap/AppArmor worker environment is unavailable. They remain ordinary, mandatory tests in the unchanged native CI workflow; no skip or sandbox bypass was added to the repository. No new GitHub CI run, Python 3.11 check, coverage gate or TradingView oracle run was completed for this candidate.

There are **115 added parameterized cases**: 73 engine and 42 OpenPine. Of these, 113 passed locally; two require the real process environment. A separate 40-case strategy-only run overlaps the 258 native cases and must not be added to the totals.

Real Pine compilation + PineLib + broker tests compare complete command tapes and resulting trades/equity between in-memory bulk and interactive transports. Cases include netting, same-callback cancellation, OCA cancellation, bracket/partial exits, cancel-by-parent, each scalar driving a real order, named historical when in v1-v5, and cumulative counts after three trade deltas. The two process tests exercise the real isolated runner, not substituted IPC, when run in CI.

Whole native invocations exceeded the local execution tool limit; the completed local evidence is explicitly split into two disjoint groups. This is not a claim of a single full native/sandbox run. Earlier attempts terminated by the tool and the unavailable sandbox run are retained as diagnostic history outside the accepted pass totals.

## Compatibility references

- https://www.tradingview.com/pine-script-docs/concepts/strategies/
- https://www.tradingview.com/pine-script-docs/migration-guides/to-pine-version-6/
- https://www.tradingview.com/pine-script-reference/v4/
- https://www.tradingview.com/pine-script-reference/v5/

The documentation guided constraints and tests. No live TradingView execution or exported TradingView trade oracle was used. Performance was not measured. OP-01 immutable deployment, OP-08 requests, checkpoints, broader realtime and UI acceptance remain separate tasks.
