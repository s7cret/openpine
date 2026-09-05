# OP-36 branch consolidation — completed 2026-09-05

Actions run: https://github.com/s7cret/openpine/actions/runs/33978124925

Verified outcome: **11 branches became exactly 4 branches**, with seven same-name archive tags preserving the removed branch tips. Main and the two historical release heads were unchanged. RC6 was not merged into main.

| Kept branch | Head at consolidation |
| --- | --- |
| `main` | `af697b28b12b672ab46442ef9a3e9f6d241802d4` |
| `release/5.0.0rc6` | `e90d657e97bf6e7b1431e87082cae3fb9dea6272` |
| `release/v2.17` | `3c026a875c6b83609e4a3fb1183a89a22afd01ef` |
| `release/v4.0.2` | `a0b89269bed8d571faf65909e2ac4dbd091764e2` |

The RC6 head in this receipt includes documentation/maintenance on top of runtime `295a6885f1094676ae1bfdc90631814daa9e8966`, which passed integrated Python 3.11/3.13 CI. This receipt itself may advance the RC6 branch with documentation only.

## Retained archive tags

| Archive tag | Preserved original head |
| --- | --- |
| `feat/5.0-isolated-worker` | `a4dca735469a91a29939fefd01ebc73a25af971f` |
| `fix/5.0-rc5-idempotent-semantic-profile-migration` | `42e1e5b1b705f1c51f582b39f84db9fce0a7814f` |
| `fix/data-delete-semantic-profile` | `6dec3785f17e16be6272a131028e96674f8b5e28` |
| `ops/rc5-immutable-deploy-identity` | `dd2663d717e9d625d6fe01c2d2f6caf2553b3306` |
| `ops/rc6-delivery-branch-selection` | `aeb6f2d9590c0635aabf4c3c79ea0674be100236` |
| `release/5.0.0rc5` | `af697b28b12b672ab46442ef9a3e9f6d241802d4` |
| `release/5.0.0rc6-local-candidate` | `e6832d851201a9e5b70f7437f652fc7704dbaf22` |

Twelve source-to-preservation mappings and the four excluded RC5 dependency-only commits are in `RC6_BRANCH_SELECTION.json`. The exclusions do not discard history: the original commits remain reachable from the archive tags and Git bundle.

The nine maintenance tests passed again in GitHub Actions before the atomic publication. They include a concurrent branch update: the expected-ref lease aborts the entire transaction, leaving all branches and no partial archive set. The actual cleanup additionally verified the successful integrated CI run and all source-preservation rules.

The downloaded receipt was checked against its SHA256, parsed before/after refs, and verified with `git bundle verify`. Every archived tag equals its original branch SHA; the retained four heads equal their pre-cleanup values.

- Receipt/history artifact `9972925495`: SHA256 `7837238dbbaf6650ea3fdb8996e71177eb1f64601e742cb8e249b8f42fd6316e`.
- Maintenance tests artifact `9972921581`: SHA256 `c9d065626c3077d7d3794bd2a8b14dc9832b53e26aac72c82a594d5a4ec0f563`.

Only branch consolidation in `s7cret/openpine` is closed by this receipt. It does not claim cleanup of sibling repositories, final production acceptance, completion of all 36 review tasks, or TradingView 1:1 parity.
