# Stage 2 scalar version and independent expected candidate

This candidate corrects the exact callable `float` interval to Pine v4-v6 and
expands separately locked manual numeric examples. Stage 2 remains in progress.
No full builtin acceptance or TradingView execution parity is claimed.

## Version authority and owners

The official [Pine v4 type-system introduction](https://www.tradingview.com/pine-script-docs/v4/language/type-system/#type-casting)
dates explicit cast functions to v4. Pine2AST retains all six signature rows,
marks their introduction and exposes `candidate_is_active`. Inactive calls fail
frontend admission; the type constant remains available. PineLib publishes the
same v4-v6 ABI interval. OpenPine delegates availability to the producer and
retains unavailable rows with an explicit reason. Compiler production code is
unchanged in this scalar block.

## Frozen expected and validation

`verification/builtin-scalars-v1` has 75 manual cases for abs, ceil, floor, na
and float across the applicable versions and overloads. Five paths exercise
ABI, historical, realtime, abort/retry and checkpoint restoration: 375 execution
checks plus six corpus/comparison checks pass on each local Python 3.11/3.13.
Twelve typed NA expectations are explicitly inferred from general documentation;
they are not individual-function TradingView observations. The previous 47-case
corpus and all its expected bytes remain unchanged.

The combined old runner and new availability suite has 266 cases: 260 pass and
six graph fixtures cannot import native `fcntl` on Windows, on both interpreters.
No skip or test deletion substitutes for the required Linux execution. Parser
targeted443, runtime34 and compiler146 checks pass on both interpreters; full
Windows owner results and initial failures remain in the attached receipts.

The shared runner adds explicit per-bar ABI arguments, strict result types and
the engineering NA token conversion; old test function/expected assertions are
preserved. Source changes, new tests and independently justified old expectation
or setup corrections are separate commits. Full Git trees are reconstructed
against immutable baselines, excluding later input and abort/control work.

## Acceptance remaining

The exact linked candidate still requires collection with a retained inventory,
all eight suites on Linux Python 3.11/3.13, real AppArmor/bwrap protected workers,
unchanged architecture and Stage 1 gates, frontend tests and builds. Publication
to feature refs is not integration into `release/5.0.0rc6` or stage acceptance.

## Reviewed scalar inventory

Collection run34167536275 checked exact sourceff93520 against registry6bc502.
Both Python proposals are byte-identical:7754 selected cases, all7243 retained,
511 added (PineLib20, Pine2AST71, Ast2Python21 and OpenPine399). No removal,
skip or additional provider deselection occurred. The 32 immutable baseline and
candidate archive trees reproduce the reviewed Git source overlays. Proposal
SHA256:5a975b2efa88f94bc0d8617434f9f9bf6c70659913da16f18790e377d08c864f.

The later completed registry publication is merged into this candidate without
changing scalar implementation or pins. This inventory review is collection-only;
the full7754-case Linux execution and aggregate gates remain pending.
