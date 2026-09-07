# Stage 2 nominal registry joint publication

The artifact-admitted nominal registry now binds enum members, UDT field schemas
and varip permissions to the compiled source declaration closure. Checkpoint
admission reuses that immutable owner registry, including nested compiled request
contexts. The compiler takes an explicit typed admission factory supplied by the
host; it no longer imports runtime code. Existing architecture policy is unchanged.

The exact candidate `6bc502705d7f0210fc87e757a9d378fb07ad9c15`, tree
`3b1e7276a9badaf6fcfb4c31884b7454c2c1d3d1`, passed joint Linux run
[34166526575](https://github.com/s7cret/openpine/actions/runs/34166526575).
Each of Python 3.11 and 3.13 executed 7243 selected tests across eight owners,
with zero failures, errors or skips. All 6745 prior cases remain; 498 were added.
The five existing provider network deselections remain unchanged. Actual native
AppArmor/bwrap protected-worker checks, architecture, conformance, distribution
builds and the unchanged Stage 1 gate passed. Frontend passed 152 Vitest and
22 Node tests and its build. All 47 manual builtin cases passed through five
paths per interpreter, retaining their immutable expected data.

The previous run34163246799 remains recorded as failed: its functional tests
passed, but the compiler-to-runtime dependency violated the architecture gate.
The explicit factory repair and its 25 additional tests are separate commits;
the policy and expected numerical assertions were preserved.

## Exact tree integration

| Owner | Two-parent release integration | Tested source |
| --- | --- | --- |
| pinelib | `85e76ec5eab106b8e5700f8d6ee2efe10ebef127` | `2a5dce3b5e2878c6d8c3c9004db65354d1ef425e` |
| ast2python | `700682f5ffb9c343462c1a23db58a7e6e904fa62` | `d3bc3e68f5da52234013e922a62e22f97835e61b` |
| OpenPine | `ceca4853caa4a07419e9d14cfafcc77e172e3c6d` | `6bc502705d7f0210fc87e757a9d378fb07ad9c15` |

All three integrations retain the prior release as first parent and the tested
feature tip as second parent. Their full trees equal the verified trees; release
refs advanced without force. The source pins still name the exact tested source
commits. This publication is a separate documentation commit.

The independent execution review and joint receipt retain artifact digests,
inventories, source identities and the earlier failure. Archival and guarded
deletion of merged temporary registry branches are pending in this receipt.

## Remaining Stage 2 work

Stage 2 remains in progress. This block does not authenticate attacker-replaced
checkpoints or establish complete Pine/TradingView equivalence. The later scalar
oracle, input qualifier, abort/control and parameter inference changes are not
part of this verified tree. Catalog, language/import matrix and builtin expected
criteria must all be satisfied before proceeding to Stage 3.
