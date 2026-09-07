# Stage 2 builtin evidence publication

The bounded builtin block passed the complete coordinated Linux gate
[34159064376](https://github.com/s7cret/openpine/actions/runs/34159064376).
The tested host is `091a88668414a12fe0a2c2e13ff11715075562f8`, tree
`c67b12cb78431d576e52636a1bd2dd8e1055c34f`. Operations commit
`1db2d1178b543a60ebc21c19a4a808141f5d5ad5` supplied the reviewed workflow.
Both backend jobs and frontend checked out that exact host. All permanent
test and acceptance commands were retained.

| Component | Python 3.11 | Python 3.13 |
|---|---:|---:|
| Contracts | 557 | 557 |
| PineLib | 591 | 591 |
| Pine2AST | 1107 | 1107 |
| Ast2Python | 832 | 832 |
| Backtest engine | 1102 | 1102 |
| Optimizer | 281 | 281 |
| Deterministic provider | 601 | 601 |
| OpenPine | 1674 | 1674 |
| **Total passed** | **6745** | **6745** |

Actual JUnit names match the reviewed inventory exactly on both interpreters.
All sixteen files contain zero failures, errors and skips. All 5842 baseline
tests remain and all 903 additions ran. The five pre-existing provider
external live-network deselections are unchanged. AppArmor/bubblewrap workers,
architecture, frozen Stage 1 corpus, capabilities, imports and package builds
passed. Frontend passed 152 Vitest tests, 22 Node tests and the production build.

All 47 manually derived builtin examples passed through direct ABI, compiled
historical, compiled realtime, abort/retry rollback and checkpoint restore on
each Python version. The 235 observations and 15 report payloads are identical
between interpreters. Exact signature assignments, source/settings/input/expected
hashes and trace tolerances were independently checked. The raw PineLib manifest
hash `sha256:739bcc3e7167e49ecbb1f37c76b4fdb4763fad724364ba9ab1cebc989e17d9aa`
and compiler projection hash
`sha256:71fdc1f483eb831ef8d21fe05b3d5858f1c55369cbe5b5dac55bfa985185dace`
identify different owned representations; observation target identities use the
compiler projection.

| Repository | Tested candidate | Release integration (same tree) |
|---|---|---|
| pine2ast | `d2b4a0f9796e23b33025d10a4918317fde4608ad` | `61c503259744d1faa88959809776f4512d48c516` |
| pinelib | `c90c267c24d4c8dfd8d292995248d09e32e19f92` | `7b53a898c79ca57cdc4b6f01d0df2a2b6704af0f` |
| ast2python | `028b2f42ac816200aedf7837a7fc7f55b3b668cc` | `72a8148e50e6f445da48297ea592e7fe399ec1f2` |
| openpine | `091a88668414a12fe0a2c2e13ff11715075562f8` | `a08c2d9d1fae143159304f3a1868fbbaf8b27e63` |

Immediately before publication all four current release refs were re-read and
matched the reviewed parents. Each update was a fast-forward through a separate
two-parent merge with the exact tested candidate tree. Functional commits, new
tests, independent corrections of old nz/round expectations and documentation
commits remain in history. The other four component pins are unchanged.
This publication commit adds only documentation, progress and receipts.

`verification/builtin-joint-review.json` contains the independent JUnit, artifact,
corpus and source review. `verification/builtin-joint-receipt.json` records exact
integration trees and parents. Artifact names are `rc6-checks-3.11-34159064376`,
`rc6-checks-3.13-34159064376` and `rc6-ui-34159064376`; copies are preserved under
`.runtime/evidence/builtin-verify-*`. Branch cleanup completed in four separate workflows. Nine archive tags were
read back before eight exact candidate/workflow refs were deleted atomically
within each repository. All four release refs remained unchanged.
`verification/builtin-cleanup-receipt.json` records the run IDs, artifact hashes,
archive identities and remote postconditions; the operations branch remains for
the next CI wave.

Stage 2 remains **in_progress**, `full_stage2_accepted=false`. The corpus covers
41 of 2374 installed callable signatures; 2333 still lack passing examples.
It does not establish complete versioned contracts, NA/warmup/edge behavior,
TradingView execution parity, full library method imports, recursive varip
references or nominal checkpoint declaration admission. The example called
`round-negative-precision` means negative input at precision **+1**, not a test
of negative precision. Local nominal registry work is outside this verified
source and receipt. Stage 3 has not started. No performance result is claimed.
